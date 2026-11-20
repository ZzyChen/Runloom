from app.storage.sqlite_store import SQLiteStore
import json
sqlitestore = SQLiteStore()
sqlitestore.initialize()
# sqlitestore.create_run("run_001","计算一下2+3")
# sqlitestore.finish_run("run_001","2+3=5")
run = sqlitestore.get_run("run_001")
events = sqlitestore.list_events("run_001")
print(json.dumps(run,indent=2,ensure_ascii=False))
print(json.dumps(events,indent=2,ensure_ascii=False))