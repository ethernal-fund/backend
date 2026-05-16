"""
Router aggregator — centraliza todos los routers.
Cada router se encarga de una parte específica de la API, como usuarios, 
fondos, tesorería, protocolos, administración, contacto, encuestas y faucet. 
Al incluir cada router en el `api_router`, se organiza la estructura de la API 
de manera modular y fácil de mantener. 
Esto permite que cada sección de la API pueda ser desarrollada y actualizada de forma independiente 
sin afectar a las demás partes.
"""

from fastapi import APIRouter

from . import (
    users,
    funds,
    treasury,
    protocols,
    admin,
    contact,
    survey,
    faucet,
)

api_router = APIRouter()

api_router.include_router(users.router)
api_router.include_router(funds.router)
api_router.include_router(treasury.router)
api_router.include_router(protocols.router)
api_router.include_router(admin.router)
api_router.include_router(contact.router)
api_router.include_router(survey.router)
api_router.include_router(faucet.router, prefix="/faucet")