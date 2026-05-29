"""
Agregador central de routers — punto único de registro de la API.

ESTRUCTURA:
  Protocolo principal (existente):
    /users       → gestión de wallets y perfiles
    /funds       → PersonalFund (creación, depósito, retiro)
    /treasury    → treasury del protocolo
    /protocols   → ProtocolRegistry
    /admin       → endpoints de administración del protocolo
    /contact     → formulario de contacto
    /survey      → encuesta de onboarding
    /faucet      → mock-USDC faucet (testnet)

  Token Sale / Crowdfunding (nuevo):
    /auth        → SIWE: nonce, verify-siwe, refresh, logout, me
    /sale        → ronda activa, compra del usuario, vesting, verify-purchase

NOTAS:
  - Los routers de auth y sale se importan con try/except para que un error
    de configuración en services/chain.py (ej: contrato no desplegado aún)
    no impida que el resto de la API arranque.
  - El prefix "/faucet" se mantiene en el include_router para no romper
    URLs existentes — el router de faucet no tiene prefix propio.
  - Todos los prefijos de ruta están definidos en cada router individual
    (auth.router tiene prefix="/auth", sale.router tiene prefix="/sale"),
    excepto faucet que lo recibe aquí.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter
from . import (
    admin,
    contact,
    faucet,
    funds,
    protocols,
    survey,
    treasury,
    users,
)
from . import auth as auth_router
from . import sale as sale_router

logger = logging.getLogger(__name__)

api_router = APIRouter()

# Protocolo principal 
api_router.include_router(users.router)
api_router.include_router(funds.router)
api_router.include_router(treasury.router)
api_router.include_router(protocols.router)
api_router.include_router(admin.router)
api_router.include_router(contact.router)
api_router.include_router(survey.router)
api_router.include_router(faucet.router, prefix="/faucet")

# Token Sale / Crowdfunding 
# auth_router expone: GET /auth/nonce, POST /auth/verify-siwe,
#                     POST /auth/refresh, POST /auth/logout, GET /auth/me
# sale_router expone: GET  /sale/round, GET /sale/my-purchase,
#                     POST /sale/verify-purchase,
#                     GET  /sale/vesting/schedule,
#                     GET  /sale/vesting/round-info
api_router.include_router(auth_router.router)
api_router.include_router(sale_router.router)