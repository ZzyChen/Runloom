from app.storage.sqlite_store import SQLiteStore

sqlitestore = SQLiteStore()
sqlitestore.initialize()
sqlitestore.create_run("run_001","计算一下2+3")