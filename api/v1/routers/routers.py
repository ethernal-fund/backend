"""
Central router aggregator for all API endpoints.
This keeps main.py clean and maintainable.
"""

from fastapi import APIRouter

# Import all endpoint routers
from api.v1.endpoints import (
    users,
    funds,
    treasury,
    protocols,
    admin,
    contact,
    survey,
    faucet,
)

# Create main API router
api_router = APIRouter()

# Include all routers
api_router.include_router(users.router,     prefix="/users")
api_router.include_router(funds.router,     prefix="/funds")
api_router.include_router(treasury.router,  prefix="/treasury")
api_router.include_router(protocols.router, prefix="/protocols")
api_router.include_router(admin.router,     prefix="/admin")
api_router.include_router(contact.router,   prefix="/contact")
api_router.include_router(survey.router,    prefix="/surveys")
api_router.include_router(faucet.router,    prefix="/faucet")   

# For backward compatibility / direct access if needed
users_router = users.router
funds_router = funds.router
treasury_router = treasury.router
protocols_router = protocols.router
admin_router = admin.router
contact_router = contact.router
survey_router = survey.router
faucet_router = faucet.router