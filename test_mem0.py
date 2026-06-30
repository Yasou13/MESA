import os

os.environ["OPENAI_API_KEY"] = (
    "sk-fake"  # prevent initialization errors if it requires one
)
try:
    from mem0 import Memory

    config = {
        "vector_store": {
            "provider": "qdrant",
            "config": {"path": "./storage/test_qdrant"},
        }
    }
    m = Memory.from_config(config_dict=config)
    res = m.add("I like apples", user_id="user1")
    print("Add:", res)
    s = m.search("What do I like?", user_id="user1")
    print("Search:", s)
    m.delete_all(user_id="user1")
    print("Deleted")
except Exception:
    import traceback

    traceback.print_exc()
