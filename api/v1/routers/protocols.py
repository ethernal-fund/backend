from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from typing import Optional
from api.db.session import get_db
from api.db.repositories.protocol_repo import ProtocolRepository
from api.core.dependencies import require_admin
from api.services.blockchain_service import BlockchainService
import logging

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/protocols", tags=["protocols"])

@router.get("/")
async def list_protocols(
    active_only: bool = Query(True),
    risk_level: Optional[int] = Query(None, ge=1, le=3, description="1=LOW 2=MEDIUM 3=HIGH"),
    db: AsyncSession = Depends(get_db),
):
    repo      = ProtocolRepository(db)
    protocols = await repo.get_all_active(risk_level=risk_level) if active_only else await repo.get_all()

    return {
        "protocols": [
            {
                "protocol_address": p.protocol_address,
                "name":             p.name,
                "apy":              float(p.apy),
                "risk_level":       p.risk_level,
                "risk_label":       {1: "LOW", 2: "MEDIUM", 3: "HIGH"}.get(p.risk_level, "UNKNOWN"),
                "is_active":        p.is_active,
                "is_verified":      p.is_verified,
                "total_deposited":  float(p.total_deposited),
                "added_at":         p.added_at,
            }
            for p in protocols
        ],
        "count": len(protocols),
    }

@router.get("/stats")
async def get_registry_stats():
    try:
        blockchain = BlockchainService()
        return await blockchain.get_protocol_registry_stats()
    except Exception as e:
        logger.error(f"Protocol registry stats failed: {e}")
        raise HTTPException(status_code=502, detail="Failed to fetch protocol stats")

@router.get("/{protocol_address}")
async def get_protocol(
    protocol_address: str,
    db: AsyncSession = Depends(get_db),
):
    repo     = ProtocolRepository(db)
    protocol = await repo.get_by_address(protocol_address)
    if not protocol:
        raise HTTPException(status_code=404, detail="Protocol not found")

    return {
        "protocol_address": protocol.protocol_address,
        "name":             protocol.name,
        "apy":              float(protocol.apy),
        "risk_level":       protocol.risk_level,
        "risk_label":       {1: "LOW", 2: "MEDIUM", 3: "HIGH"}.get(protocol.risk_level, "UNKNOWN"),
        "is_active":        protocol.is_active,
        "is_verified":      protocol.is_verified,
        "total_deposited":  float(protocol.total_deposited),
        "added_at":         protocol.added_at,
        "last_updated_at":  protocol.last_updated_at,
        "synced_at":        protocol.synced_at,
    }

@router.post("/sync")
async def sync_protocols(
    admin: str = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    try:
        blockchain = BlockchainService()
        repo       = ProtocolRepository(db)

        protocols = await blockchain.get_all_protocols()
        synced    = 0
        for p in protocols:
            await repo.upsert_from_chain(p)
            synced += 1
        return {"success": True, "synced": synced}
    except Exception as e:
        logger.error(f"Protocol sync failed: {e}")
        raise HTTPException(status_code=502, detail=str(e))