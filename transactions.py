"""
Transaction tracking utilities for payment and subscription purchases.

This module provides functions to:
- Record all payment transactions in a separate 'transactions' MongoDB collection
- Track Google Play, App Store, and other payment platform transactions
- Store comprehensive metadata for future analytics and reporting
- Support audit trails and revenue tracking

Collection: transactions
Purpose: Immutable record of all payment transactions for reporting and analytics
"""
from __future__ import annotations
from datetime import datetime, timedelta
from typing import Dict, Any, List, Optional
import uuid


def _now() -> datetime:
    """Return current UTC datetime."""
    return datetime.utcnow()


def _iso(dt: datetime) -> str:
    """Convert datetime to ISO format string."""
    return dt.isoformat() + "Z"


async def create_transaction(
    user_id: str,
    plan_id: str,
    amount: float,
    currency: str,
    payment_method: str,
    db,
    **kwargs
) -> str:
    """
    Create a new transaction record in the transactions collection.
    
    Args:
        user_id: User making the purchase
        plan_id: Subscription plan ID
        amount: Amount paid in the specified currency
        currency: Currency code (INR, USD, etc)
        payment_method: google_play, app_store, stripe, razorpay, etc
        db: MongoDB database connection
        **kwargs: Additional optional fields (product_id, purchase_token, etc)
    
    Returns:
        transaction_id: Unique transaction identifier
    """
    from subscription import _PLANS
    
    # Generate unique transaction ID
    transaction_id = str(uuid.uuid4())
    
    # Get plan details
    plan = _PLANS.get(plan_id, {})
    plan_name = plan.get("name", plan_id)
    duration_days = plan.get("duration_days")
    image_quota = plan.get("image_quota")
    
    # Calculate USD amount (for reporting)
    # Approximate conversion rates (should use real-time rates in production)
    conversion_rates = {
        "INR": 0.012,  # 1 INR ≈ 0.012 USD
        "USD": 1.0,
        "EUR": 1.08,
        "GBP": 1.27,
    }
    amount_usd = amount * conversion_rates.get(currency, 1.0)
    
    # Calculate subscription dates
    now = _now()
    start_date = now
    end_date = now + timedelta(days=duration_days) if duration_days else None
    
    # Get user details
    users_coll = db.get_collection("users")
    uid_filter = {"_id": __import__("bson").ObjectId(user_id)} if len(user_id) == 24 else {"_id": user_id}
    user_doc = await users_coll.find_one(uid_filter)
    user_email = user_doc.get("email") if user_doc else None
    
    # Build transaction document
    transaction = {
        "transaction_id": transaction_id,
        "user_id": user_id,
        "user_email": user_email,
        "plan_id": plan_id,
        "plan_name": plan_name,
        "product_id": kwargs.get("product_id"),
        
        # Payment details
        "amount": amount,
        "currency": currency,
        "amount_usd": amount_usd,
        "payment_method": payment_method,
        
        # Platform-specific IDs
        "purchase_token": kwargs.get("purchase_token"),
        "order_id": kwargs.get("order_id"),
        "receipt_data": kwargs.get("receipt_data"),
        "session_id": kwargs.get("session_id"),
        
        # Subscription details
        "subscription_start_date": _iso(start_date),
        "subscription_end_date": _iso(end_date) if end_date else None,
        "duration_days": duration_days,
        "image_quota": image_quota,
        
        # Status tracking
        "status": kwargs.get("status", "pending"),
        "verified": kwargs.get("verified", False),
        
        # Timestamps
        "created_at": _iso(now),
        "completed_at": None,
        "updated_at": _iso(now),
        
        # Metadata
        "device_platform": kwargs.get("device_platform"),
        "app_version": kwargs.get("app_version"),
        "country_code": kwargs.get("country_code"),
        "ip_address": kwargs.get("ip_address"),
        
        # Additional notes
        "notes": kwargs.get("notes"),
    }
    
    # Insert into transactions collection
    trans_coll = db.get_collection("transactions")
    await trans_coll.insert_one(transaction)
    
    return transaction_id


async def update_transaction_status(
    transaction_id: str,
    status: str,
    db,
    verified: Optional[bool] = None,
    notes: Optional[str] = None
) -> bool:
    """
    Update the status of an existing transaction.
    
    Args:
        transaction_id: Transaction to update
        status: New status (completed, failed, refunded, cancelled)
        db: MongoDB database connection
        verified: Whether purchase was verified with platform
        notes: Additional notes to add
    
    Returns:
        bool: True if updated successfully
    """
    trans_coll = db.get_collection("transactions")
    
    update_doc = {
        "status": status,
        "updated_at": _iso(_now()),
    }
    
    if status == "completed" and verified is not None:
        update_doc["completed_at"] = _iso(_now())
        update_doc["verified"] = verified
    
    if notes:
        update_doc["notes"] = notes
    
    result = await trans_coll.update_one(
        {"transaction_id": transaction_id},
        {"$set": update_doc}
    )
    
    return result.modified_count > 0


async def get_user_transactions(
    user_id: str,
    db,
    limit: int = 50,
    skip: int = 0
) -> List[Dict[str, Any]]:
    """
    Get all transactions for a specific user.
    
    Args:
        user_id: User ID to fetch transactions for
        db: MongoDB database connection
        limit: Maximum number of transactions to return
        skip: Number of transactions to skip (for pagination)
    
    Returns:
        List of transaction documents
    """
    trans_coll = db.get_collection("transactions")
    
    cursor = trans_coll.find({"user_id": user_id}).sort("created_at", -1).skip(skip).limit(limit)
    transactions = await cursor.to_list(length=limit)
    
    # Remove MongoDB _id field
    for trans in transactions:
        trans.pop("_id", None)
    
    return transactions


async def get_transaction_by_id(
    transaction_id: str,
    db
) -> Optional[Dict[str, Any]]:
    """
    Get a specific transaction by ID.
    
    Args:
        transaction_id: Transaction ID to fetch
        db: MongoDB database connection
    
    Returns:
        Transaction document or None if not found
    """
    trans_coll = db.get_collection("transactions")
    transaction = await trans_coll.find_one({"transaction_id": transaction_id})
    
    if transaction:
        transaction.pop("_id", None)
    
    return transaction


async def get_revenue_stats(
    db,
    start_date: Optional[datetime] = None,
    end_date: Optional[datetime] = None
) -> Dict[str, Any]:
    """
    Get revenue statistics for a date range.
    
    Args:
        db: MongoDB database connection
        start_date: Start date for stats (optional)
        end_date: End date for stats (optional)
    
    Returns:
        Dictionary with revenue statistics
    """
    trans_coll = db.get_collection("transactions")
    
    # Build query filter
    query = {"status": "completed"}
    if start_date or end_date:
        query["completed_at"] = {}
        if start_date:
            query["completed_at"]["$gte"] = _iso(start_date)
        if end_date:
            query["completed_at"]["$lte"] = _iso(end_date)
    
    # Aggregate statistics
    pipeline = [
        {"$match": query},
        {
            "$group": {
                "_id": None,
                "total_transactions": {"$sum": 1},
                "total_revenue_usd": {"$sum": "$amount_usd"},
                "avg_transaction_usd": {"$avg": "$amount_usd"},
                "currencies": {"$addToSet": "$currency"},
                "payment_methods": {"$addToSet": "$payment_method"},
            }
        }
    ]
    
    result = await trans_coll.aggregate(pipeline).to_list(length=1)
    
    if result:
        stats = result[0]
        stats.pop("_id", None)
        return stats
    
    return {
        "total_transactions": 0,
        "total_revenue_usd": 0,
        "avg_transaction_usd": 0,
        "currencies": [],
        "payment_methods": [],
    }
