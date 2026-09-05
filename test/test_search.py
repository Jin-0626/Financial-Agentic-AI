import asyncio

from agents.tools.bursa_rag import search_bursa_notes


async def main():
    query = "optical segment revenue performance and retail outlet expansion"
    print(f"[*] Querying pgvector for Focus Point (0157): '{query}'\n")

    results = await search_bursa_notes("0157", query, limit=3)
    for i, r in enumerate(results, 1):
        print(f"--- Result {i} [{r['section']}] (Sim: {r['similarity']}) ---")
        print(r["chunk"][:280] + "...\n")


if __name__ == "__main__":
    asyncio.run(main())
