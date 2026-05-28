from web3 import Web3
from web3.middleware import ExtraDataToPOAMiddleware  # Base/Polygon
import asyncio, os

w3 = Web3(Web3.HTTPProvider(os.getenv("RPC_URL")))

SALE_ABI = [...]  # solo el evento que te interesa
contract = w3.eth.contract(address=os.getenv("SALE_ADDRESS"), abi=SALE_ABI)

async def start_listener():
    # 1. Histórico: desde el último bloque procesado
    last_block = await get_last_indexed_block()  # query a Supabase
    current   = w3.eth.block_number
    
    CHUNK = 2000
    for start in range(last_block, current, CHUNK):
        end = min(start + CHUNK - 1, current)
        logs = contract.events.TokensPurchased.get_logs(
            fromBlock=start, toBlock=end
        )
        for log in logs:
            await upsert_raw_event(log)  # idempotente por tx_hash+log_index
    
    # 2. Tiempo real con filtro
    event_filter = contract.events.TokensPurchased.create_filter(
        fromBlock="latest"
    )
    while True:
        for log in event_filter.get_new_entries():
            await upsert_raw_event(log)
        await asyncio.sleep(3)  # polling cada 3s