"""
Payment processing module for Pickoo AI backend.
Handles Stripe integration, currency detection, and payment tracking in MongoDB.
"""
import stripe
import logging
from datetime import datetime, timezone
from typing import Optional, Dict, Any
from motor.motor_asyncio import AsyncIOMotorClient
from config import settings

# Initialize Stripe
stripe.api_key = settings.stripe_secret_key

logger = logging.getLogger(__name__)

# Currency mapping for different countries
CURRENCY_MAP = {
    "US": "usd", "GB": "gbp", "EU": "eur", "CA": "cad", "AU": "aud",
    "IN": "inr", "JP": "jpy", "CN": "cny", "SG": "sgd", "HK": "hkd",
    "NZ": "nzd", "CH": "chf", "SE": "sek", "NO": "nok", "DK": "dkk",
    "MX": "mxn", "BR": "brl", "ZA": "zar", "AE": "aed", "SA": "sar",
    "KR": "krw", "TH": "thb", "MY": "myr", "PH": "php", "ID": "idr",
}

# Price conversion rates (base USD)
PRICE_CONVERSION = {
    "usd": 1.0, "eur": 0.92, "gbp": 0.79, "cad": 1.36, "aud": 1.52,
    "inr": 83.0, "jpy": 149.0, "cny": 7.24, "sgd": 1.34, "hkd": 7.83,
    "nzd": 1.65, "chf": 0.88, "sek": 10.45, "nok": 10.75, "dkk": 6.88,
    "mxn": 17.0, "brl": 4.97, "zar": 18.5, "aed": 3.67, "sar": 3.75,
    "krw": 1315.0, "thb": 35.5, "myr": 4.68, "php": 56.0, "idr": 15600.0,
}


class PaymentService:
    """Service for handling payment operations with Stripe and MongoDB."""
    
    def __init__(self):
        """Initialize payment service with MongoDB connection."""
        self.client: Optional[AsyncIOMotorClient] = None
        self.db = None
        self.payments_collection = None
        
    async def connect_db(self):
        """Connect to MongoDB for payment tracking."""
        if not self.client:
            self.client = AsyncIOMotorClient(settings.mongo_uri)
            self.db = self.client.get_default_database()
            self.payments_collection = self.db["payments"]
            logger.info("Payment service connected to MongoDB")
    
    async def close_db(self):
        """Close MongoDB connection."""
        if self.client:
            self.client.close()
            logger.info("Payment service disconnected from MongoDB")
    
    def get_currency_for_country(self, country_code: str) -> str:
        """
        Get currency code for a country.
        
        Args:
            country_code: ISO 3166-1 alpha-2 country code (e.g., "US", "IN")
            
        Returns:
            Currency code (e.g., "usd", "inr")
        """
        return CURRENCY_MAP.get(country_code.upper(), "usd")
    
    def convert_price(self, base_price_usd: float, currency: str) -> int:
        """
        Convert USD price to target currency.
        Returns amount in smallest currency unit (cents/paise).
        
        Args:
            base_price_usd: Price in USD
            currency: Target currency code
            
        Returns:
            Price in smallest currency unit (e.g., cents for USD)
        """
        rate = PRICE_CONVERSION.get(currency.lower(), 1.0)
        converted = base_price_usd * rate
        
        # For zero-decimal currencies (JPY, KRW, etc.), return as-is
        if currency.lower() in ["jpy", "krw", "idr", "clp", "pyg", "vnd"]:
            return int(converted)
        
        # For other currencies, multiply by 100 to get smallest unit
        return int(converted * 100)
    
    async def create_checkout_session(
        self,
        user_id: str,
        plan_id: str,
        plan_name: str,
        base_price_usd: float,
        currency: str = "usd",
        success_url: str = None,
        cancel_url: str = None,
    ) -> Dict[str, Any]:
        """
        Create a Stripe Checkout session for subscription payment.
        
        Args:
            user_id: User ID making the purchase
            plan_id: Subscription plan ID
            plan_name: Display name of the plan
            base_price_usd: Base price in USD
            currency: Currency code
            success_url: URL to redirect after successful payment
            cancel_url: URL to redirect after cancelled payment
            
        Returns:
            Dictionary with session details including checkout URL
        """
        try:
            await self.connect_db()
            
            # Convert price to target currency
            amount = self.convert_price(base_price_usd, currency)
            
            # Create Stripe Checkout session
            session = stripe.checkout.Session.create(
                payment_method_types=["card"],
                line_items=[{
                    "price_data": {
                        "currency": currency,
                        "product_data": {
                            "name": f"Pickoo AI - {plan_name}",
                            "description": f"Subscription to {plan_name} plan",
                        },
                        "unit_amount": amount,
                    },
                    "quantity": 1,
                }],
                mode="payment",
                success_url=success_url or settings.payment_success_url,
                cancel_url=cancel_url or settings.payment_cancel_url,
                client_reference_id=user_id,
                metadata={
                    "user_id": user_id,
                    "plan_id": plan_id,
                    "plan_name": plan_name,
                    "base_price_usd": str(base_price_usd),
                    "currency": currency,
                },
            )
            
            # Store payment intent in MongoDB
            payment_record = {
                "user_id": user_id,
                "session_id": session.id,
                "plan_id": plan_id,
                "plan_name": plan_name,
                "amount": amount,
                "currency": currency,
                "base_price_usd": base_price_usd,
                "status": "pending",
                "created_at": datetime.now(timezone.utc),
                "updated_at": datetime.now(timezone.utc),
                "stripe_session_url": session.url,
            }
            
            await self.payments_collection.insert_one(payment_record)
            logger.info(f"Created payment session for user {user_id}: {session.id}")
            
            return {
                "session_id": session.id,
                "checkout_url": session.url,
                "amount": amount,
                "currency": currency,
            }
            
        except stripe.error.StripeError as e:
            logger.error(f"Stripe error creating checkout session: {e}")
            raise Exception(f"Payment processing error: {str(e)}")
        except Exception as e:
            logger.error(f"Error creating checkout session: {e}")
            raise
    
    async def handle_webhook_event(self, payload: bytes, signature: str) -> Dict[str, Any]:
        """
        Handle Stripe webhook events for payment confirmation.
        
        Args:
            payload: Raw webhook payload
            signature: Stripe signature header
            
        Returns:
            Dictionary with event processing status
        """
        try:
            # Verify webhook signature
            event = stripe.Webhook.construct_event(
                payload, signature, settings.stripe_webhook_secret
            )
            
            await self.connect_db()
            
            # Handle different event types
            if event["type"] == "checkout.session.completed":
                session = event["data"]["object"]
                await self._handle_successful_payment(session)
                
            elif event["type"] == "checkout.session.expired":
                session = event["data"]["object"]
                await self._handle_expired_session(session)
                
            elif event["type"] == "payment_intent.payment_failed":
                payment_intent = event["data"]["object"]
                await self._handle_failed_payment(payment_intent)
            
            return {"status": "success", "event_type": event["type"]}
            
        except stripe.error.SignatureVerificationError as e:
            logger.error(f"Invalid webhook signature: {e}")
            raise Exception("Invalid signature")
        except Exception as e:
            logger.error(f"Error handling webhook: {e}")
            raise
    
    async def _handle_successful_payment(self, session: Dict[str, Any]):
        """Handle successful payment completion."""
        session_id = session["id"]
        user_id = session.get("client_reference_id") or session["metadata"].get("user_id")
        
        # Update payment record in MongoDB
        update_data = {
            "status": "completed",
            "payment_status": session.get("payment_status"),
            "amount_total": session.get("amount_total"),
            "updated_at": datetime.now(timezone.utc),
            "completed_at": datetime.now(timezone.utc),
        }
        
        await self.payments_collection.update_one(
            {"session_id": session_id},
            {"$set": update_data}
        )
        
        logger.info(f"Payment completed for session {session_id}, user {user_id}")
    
    async def _handle_expired_session(self, session: Dict[str, Any]):
        """Handle expired checkout session."""
        session_id = session["id"]
        
        await self.payments_collection.update_one(
            {"session_id": session_id},
            {"$set": {
                "status": "expired",
                "updated_at": datetime.now(timezone.utc),
            }}
        )
        
        logger.info(f"Payment session expired: {session_id}")
    
    async def _handle_failed_payment(self, payment_intent: Dict[str, Any]):
        """Handle failed payment attempt."""
        # Find payment by payment intent ID
        await self.payments_collection.update_one(
            {"payment_intent_id": payment_intent["id"]},
            {"$set": {
                "status": "failed",
                "failure_message": payment_intent.get("last_payment_error", {}).get("message"),
                "updated_at": datetime.now(timezone.utc),
            }}
        )
        
        logger.warning(f"Payment failed for intent {payment_intent['id']}")
    
    async def get_user_payments(self, user_id: str) -> list:
        """
        Get all payment records for a user.
        
        Args:
            user_id: User ID to query
            
        Returns:
            List of payment records
        """
        await self.connect_db()
        
        cursor = self.payments_collection.find(
            {"user_id": user_id}
        ).sort("created_at", -1)
        
        payments = await cursor.to_list(length=100)
        
        # Convert ObjectId to string for JSON serialization
        for payment in payments:
            payment["_id"] = str(payment["_id"])
        
        return payments
    
    async def get_payment_by_session(self, session_id: str) -> Optional[Dict[str, Any]]:
        """
        Get payment record by Stripe session ID.
        
        Args:
            session_id: Stripe checkout session ID
            
        Returns:
            Payment record or None
        """
        await self.connect_db()
        
        payment = await self.payments_collection.find_one({"session_id": session_id})
        
        if payment:
            payment["_id"] = str(payment["_id"])
        
        return payment


# Global payment service instance
payment_service = PaymentService()
