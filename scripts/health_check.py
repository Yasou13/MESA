import os
import sys
import urllib.error
import urllib.request


def check_api_health():
    url = "http://localhost:8000/v3/health"
    try:
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=5) as response:
            if response.status == 200:
                print("✅ API Health Check (/v3/health)")
                return True
            else:
                print(f"❌ API Health Check failed with status {response.status}")
                return False
    except Exception as e:
        print(f"❌ API Health Check failed: {e}")
        return False


def check_kuzu():
    try:
        import kuzu

        db_path = os.getenv(
            "KUZU_DB_PATH",
            os.path.abspath(os.path.join(os.getcwd(), "storage/kuzu_db")),
        )
        if not os.path.exists(os.path.dirname(db_path)):
            os.makedirs(os.path.dirname(db_path), exist_ok=True)
        kuzu.Database(db_path)
        print("✅ KuzuDB Connection")
        return True
    except ImportError:
        print("❌ KuzuDB not installed")
        return False
    except Exception as e:
        print(f"❌ KuzuDB Connection failed: {e}")
        return False


def check_lancedb():
    try:
        import lancedb

        db_path = os.getenv(
            "LANCE_DB_PATH",
            os.path.abspath(os.path.join(os.getcwd(), "storage/vector_index.lance")),
        )
        if not os.path.exists(os.path.dirname(db_path)):
            os.makedirs(os.path.dirname(db_path), exist_ok=True)
        lancedb.connect(db_path)
        print("✅ LanceDB Connection")
        return True
    except ImportError:
        print("❌ LanceDB not installed")
        return False
    except Exception as e:
        print(f"❌ LanceDB Connection failed: {e}")
        return False


def main():
    print("Running MESA Health Checks...")
    passed = True

    if not check_api_health():
        passed = False

    if not check_kuzu():
        passed = False

    if not check_lancedb():
        passed = False

    if passed:
        print("🎉 All health checks passed!")
        sys.exit(0)
    else:
        print("⚠️ Some health checks failed.")
        sys.exit(1)


if __name__ == "__main__":
    main()
