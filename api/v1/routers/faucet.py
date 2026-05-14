# Este endpoint recibe la solicitud del frontend y la reenvía al servidor
# del faucet server-to-server (sin problema de CORS).

import httpx
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

router = APIRouter()


class FaucetProxyRequest(BaseModel):
    address:   str
    chainId:   int = 421614
    faucetUrl: str  # sent by the frontend client
    network:   str  # sent by the frontend client


class FaucetProxyResponse(BaseModel):
    success:      bool
    message:      str
    tx_hash:      str | None = None
    eth_tx_hash:  str | None = None
    amount:       float | None = None
    eth_amount:   float | None = None
    balance:      float | None = None
    network:      str | None = None
    wait_time:    int | None = None


@router.post("/faucet/proxy", response_model=FaucetProxyResponse)
async def faucet_proxy(body: FaucetProxyRequest) -> FaucetProxyResponse:
    """
    CORS proxy: forwards the faucet request from the browser to the faucet server.
    The faucet server only needs to allow requests from this backend's IP, not from
    arbitrary browser origins.
    """
    target_url = f"{body.faucetUrl.rstrip('/')}/faucet"

    async with httpx.AsyncClient(timeout=60.0) as client:
        try:
            resp = await client.post(
                target_url,
                json={"address": body.address, "network": body.network},
            )
            resp.raise_for_status()
            data = resp.json()
            return FaucetProxyResponse(**data)
        except httpx.TimeoutException:
            raise HTTPException(status_code=504, detail="El servidor del faucet no respondió a tiempo.")
        except httpx.HTTPStatusError as e:
            raise HTTPException(
                status_code=e.response.status_code,
                detail=e.response.text,
            )
        except Exception as e:
            raise HTTPException(status_code=502, detail=str(e))
