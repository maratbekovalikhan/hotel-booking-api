ROLE_CUSTOMER = "customer"
ROLE_RECEPTIONIST = "receptionist"
ROLE_MANAGER = "manager"
ROLE_SUPERADMIN = "superadmin"

ROLE_LABELS = {
    ROLE_CUSTOMER: "Клиент",
    ROLE_RECEPTIONIST: "Ресепшен",
    ROLE_MANAGER: "Менеджер платформы",
    ROLE_SUPERADMIN: "Суперадминистратор",
}
STAFF_ROLES = (ROLE_RECEPTIONIST, ROLE_MANAGER)

BOOKING_STATUS_LABELS = {
    "pending": "Ожидает оплаты",
    "paid": "Оплачена",
    "checked_in": "Проживает",
    "checked_out": "Выселен",
    "cancelled": "Отменена",
}
# Брони в этих статусах занимают номер
ACTIVE_BOOKING_STATUSES = ("pending", "paid", "checked_in")

HOUSEKEEPING_LABELS = {
    "clean": "Чистый",
    "dirty": "Нужна уборка",
    "cleaning": "Убирается",
}
HOTEL_STATUS_LABELS = {
    "pending": "На модерации",
    "approved": "Одобрен",
    "rejected": "Отклонён",
}
PAYMENT_STATUS_LABELS = {"paid": "Оплачено", "refunded": "Возвращено"}
