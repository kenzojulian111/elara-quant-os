from enum import Enum


class TaskStatus(str, Enum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    DONE = "done"


class TradeStatus(str, Enum):
    OPEN = "OPEN"
    CLOSED = "CLOSED"


class OrderSide(str, Enum):
    BUY = "buy"
    SELL = "sell"


class ExchangeOrderStatus(str, Enum):
    NEW = "new"
    OPEN = "open"
    PENDING = "pending"
    PARTIALLY_FILLED = "partially_filled"
    FILLED = "filled"
    CLOSED = "closed"
    CANCELED = "canceled"
    CANCELLED = "cancelled"
    EXPIRED = "expired"
    REJECTED = "rejected"

