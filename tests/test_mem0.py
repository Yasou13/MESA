"""Manual Mem0 smoke script; intentionally not a pytest collection side effect."""


def main() -> None:
    import os

    os.environ["OPENAI_API_KEY"] = "sk-fake"
    from mem0 import Memory

    config = {
        "vector_store": {
            "provider": "qdrant",
            "config": {"path": "./storage/test_qdrant"},
        }
    }
    memory = Memory.from_config(config_dict=config)
    print("Add:", memory.add("I like apples", user_id="user1"))
    print("Search:", memory.search("What do I like?", user_id="user1"))
    memory.delete_all(user_id="user1")
    print("Deleted")


if __name__ == "__main__":
    main()
