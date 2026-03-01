from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse
import logging

logger = logging.getLogger(__name__)

class EthernalException(Exception):
    def __init__(self, status_code: int, detail: str, code: str = None):
        self.status_code = status_code
        self.detail = detail
        self.code = code
        super().__init__(detail)

class WalletNotFound(EthernalException):
    def __init__(self, wallet: str):
        super().__init__(404, f"Wallet {wallet} not found", "WALLET_NOT_FOUND")

class FundNotFound(EthernalException):
    def __init__(self, identifier: str):
        super().__init__(404, f"Fund not found: {identifier}", "FUND_NOT_FOUND")

class FundAlreadyExists(EthernalException):
    def __init__(self, wallet: str):
        super().__init__(409, f"Wallet {wallet} already has a PersonalFund", "FUND_EXISTS")

class SurveyAlreadyCompleted(EthernalException):
    def __init__(self):
        super().__init__(409, "Survey already completed", "SURVEY_COMPLETED")

class InvalidSignature(EthernalException):
    def __init__(self):
        super().__init__(401, "Invalid wallet signature", "INVALID_SIGNATURE")

class BlockchainError(EthernalException):
    def __init__(self, detail: str):
        super().__init__(502, f"Blockchain error: {detail}", "BLOCKCHAIN_ERROR")

async def ethernal_exception_handler(request: Request, exc: EthernalException):
    return JSONResponse(
        status_code=exc.status_code,
        content={
            "detail": exc.detail,
            "code": exc.code,
        },
    )

async def global_exception_handler(request: Request, exc: Exception):
    logger.error(f"Unhandled exception on {request.url}: {exc}", exc_info=True)
    return JSONResponse(
        status_code=500,
        content={"detail": "Internal server error", "code": "INTERNAL_ERROR"},
    )