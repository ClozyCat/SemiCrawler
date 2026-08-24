from datetime import date

from app.database import SessionLocal
from app.models import CollectionMetric, CollectionTask, Source


def test_collection_metrics_endpoint_reports_release_counters(client):
    with SessionLocal() as db:
        source = Source(name="指标来源", base_url="https://metrics.example", config_json="{}")
        db.add(source)
        db.flush()
        task = CollectionTask(status="completed", start_date=date(2026, 8, 24),
                              source_ids_json=f"[{source.id}]", source_snapshot_json="[]")
        db.add(task)
        db.flush()
        db.add(CollectionMetric(task_id=task.id, source_id=source.id, source_name=source.name,
                                 transport="http", content_kind="table_records", duration_ms=123,
                                 pages=2, discovered=40, saved=38, deduplicated=2, failed=0,
                                 rule_repairs=0, llm_calls=0, estimated_cost=0.0,
                                 stop_reason="遇到早于起始日期的页面"))
        db.commit()
        task_id = task.id
    response = client.get(f"/api/metrics/collection?task_id={task_id}")
    assert response.status_code == 200
    data = response.json()
    assert data["summary"] == {
        "runs": 1, "duration_ms": 123, "pages": 2, "discovered": 40,
        "saved": 38, "deduplicated": 2, "failed": 0, "rule_repairs": 0,
        "llm_calls": 0, "estimated_cost": 0.0,
    }
    assert data["items"][0]["stop_reason"] == "遇到早于起始日期的页面"
