import asyncio
import os
import sys

# Add parent directory of mesa_benchmark to path to find mesa_storage & mesa_memory
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "../../")))

from mesa_memory.adapter.factory import AdapterFactory
from mesa_memory.retrieval.core import QueryAnalyzer

print("Imports done.")


async def main():
    print("Getting adapter...")
    _ = AdapterFactory.get_adapter("auto")
    print("Got adapter.")

    print("Initializing analyzer...")
    _ = QueryAnalyzer()
    print("Got analyzer.")

    print("Done.")


asyncio.run(main())
