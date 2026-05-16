# api/v1/routers/faucet.py
#
# CORS proxy: reenvía la request del frontend al mock-usdc server-to-server,
# evitando problemas de CORS en el browser. También propaga la IP real del
# usuario para que el rate limiter del mock-usdc opere por usuario, no por
# IP del backend.

import httpx
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

router = APIRouter()


class FaucetProxyRequest(BaseModel):
    address:   str
    chainId:   int = 421614
    faucetUrl: str   # enviado por el cliente frontend
    network:   str   # enviado por el cliente frontend


class FaucetProxyResponse(BaseModel):
    success:     bool
    message:     str
    tx_hash:     str | None   = None
    eth_tx_hash: str | None   = None
    amount:      float | None = None
    eth_amount:  float | None = None
    balance:     float | None = None
    network:     str | None   = None
    wait_time:   int | None   = None


@router.post("/proxy", response_model=FaucetProxyResponse)
async def faucet_proxy(
    body: FaucetProxyRequest,
    request: Request,
) -> FaucetProxyResponse:
    """
    Proxy CORS: recibe la solicitud del browser y la reenvía al mock-usdc
    server-to-server. Propaga la IP real del usuario via X-Forwarded-For
    para que el rate limiter del faucet opere por usuario y no por IP del backend.
    """
    target_url = f"{body.faucetUrl.rstrip('/')}/faucet"

    # Extraer IP real del usuario (Render pone la IP en X-Forwarded-For)
    forwarded = request.headers.get("X-Forwarded-For", "")
    client_ip = forwarded.split(",")[0].strip() if forwarded else (
        request.client.host if request.client else "unknown"
    )

    async with httpx.AsyncClient(timeout=60.0) as client:
        try:
            resp = await client.post(
                target_url,
                json={
                    "address": body.address,
                    "network": body.network,
                },
                headers={
                    "X-Forwarded-For": client_ip,
                    "X-Real-IP":       client_ip,
                },
            )
            resp.raise_for_status()
            data = resp.json()
            return FaucetProxyResponse(**data)

        except httpx.TimeoutException:
            raise HTTPException(
                status_code=504,
                detail="El servidor del faucet no respondió a tiempo.",
            )
        except httpx.HTTPStatusError as e:
            # Propagar el status code original del mock-usdc (ej: 429 rate limit)
            try:
                detail = e.response.json().get("detail", e.response.text)
            except Exception:
                detail = e.response.text
            raise HTTPException(
                status_code=e.response.status_code,
                detail=detail,
            )
        except Exception as e:
            raise HTTPException(status_code=502, detail=str(e))