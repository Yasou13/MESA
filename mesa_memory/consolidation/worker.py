import asyncio
import logging

from mesa_memory.storage import StorageFacade
from mesa_memory.consolidation.loop import ConsolidationLoop

logger = logging.getLogger("MESA_Worker")

async def start_tier3_deferred_worker(
    storage: StorageFacade,
    consolidation_loop: ConsolidationLoop,
    sleep_interval: int = 5,
    batch_size: int = 10,
):
    """
    Background worker that continuously consumes and processes
    unconsolidated records flagged with tier3_deferred=True.
    """
    logger.info("Starting Tier-3 Deferred background worker...")
    while True:
        try:
            records = await storage.raw_log.fetch_unconsolidated(limit=100)
            deferred_records = [r for r in records if r.get("tier3_deferred")]
            
            if deferred_records:
                batch = deferred_records[:batch_size]
                logger.debug(f"Worker fetched {len(deferred_records)} deferred records.")
                logger.info(f"Worker processing {len(batch)} deferred records.")
                await consolidation_loop.run_batch(batch)
                
                # Clear the tier3_deferred flag to prevent infinite loops on the same record
                for record in batch:
                    await storage.raw_log.clear_tier3_deferred(record["cmb_id"])
            else:
                await asyncio.sleep(sleep_interval)
                
        except asyncio.CancelledError:
            logger.info("Tier-3 Deferred worker cancelled, shutting down.")
            break
        except Exception as e:
            logger.error(f"Error in Tier-3 Deferred worker: {e}", exc_info=True)
            await asyncio.sleep(sleep_interval)
