with open("tests/test_retrieval.py", "r") as f:
    content = f.read()

content = content.replace(
    "storage.graph.get_active_graph.return_value = empty_graph",
    "storage.graph.get_active_graph.return_value = empty_graph\n    storage.graph.find_nodes_by_name = AsyncMock(return_value=[])\n    storage.graph.get_all_active_nodes = AsyncMock(return_value=[])"
)

with open("tests/test_retrieval.py", "w") as f:
    f.write(content)
