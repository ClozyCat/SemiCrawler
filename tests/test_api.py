from io import BytesIO

from openpyxl import load_workbook

from app.constants import EXPORT_COLUMNS
from app.models import RawArticle, StructuredRecord


def test_defaults_sources_and_meta(client):
    meta = client.get("/api/meta")
    assert meta.status_code == 200
    assert meta.json()["default_start_date"] == "2026-08-01"
    assert "资讯类型" not in meta.json()["info_types"]
    assert "项目立项" in meta.json()["info_types"]

    sources = client.get("/api/sources").json()
    assert [item["name"] for item in sources] == ["全球半导体观察（DRAMx）", "半导体产业网"]
    assert all(item["enabled"] for item in sources)


def test_source_persistence_and_toggle(client):
    response = client.post("/api/sources", json={
        "name": "测试来源", "base_url": "https://example.com",
        "enabled": True, "config": {"entry_urls": ["https://example.com/news"],
            "article_url_pattern": "/news/\\d+", "selectors": {"list_links": "a", "title": "h1", "date": ".date", "content": ".content"}},
    })
    assert response.status_code == 201
    source_id = response.json()["id"]

    updated = client.patch(f"/api/sources/{source_id}", json={"enabled": False})
    assert updated.status_code == 200
    assert updated.json()["enabled"] is False


def test_task_snapshot_and_logs(client, monkeypatch):
    def fake_run(db, task):
        from app.models import TaskLog, utc_now
        task.status = "completed"; task.completed_at = utc_now()
        db.add(TaskLog(task_id=task.id, message="任务完成：Mock")); db.commit()
    monkeypatch.setattr("app.main.run_task", fake_run)
    source_ids = [item["id"] for item in client.get("/api/sources").json()]
    response = client.post("/api/tasks", json={"source_ids": source_ids, "start_date": "2026-08-01"})
    assert response.status_code == 201
    task = response.json()
    assert task["status"] == "queued"
    task = client.get(f"/api/tasks/{task['id']}").json()
    assert task["status"] == "completed"
    assert task["progress"] == 100
    assert task["created_at"].endswith("+08:00")
    assert task["completed_at"].endswith("+08:00")
    assert len(task["source_snapshot"]) == 2

    assert task["keyword_filter_enabled"] is False
    assert task["auto_structure_enabled"] is False

    logs = client.get(f"/api/tasks/{task['id']}/logs").json()
    assert len(logs) == 4
    assert "Mock" in logs[-1]["message"]
    assert all(log["created_at"].endswith("+08:00") for log in logs)


def test_task_snapshots_keyword_and_structure_options(client, monkeypatch):
    monkeypatch.setattr("app.main.run_task", lambda db, task: None)
    source_id = client.get("/api/sources").json()[0]["id"]
    config = [{"industry": "先进封装", "field": "Chiplet", "keywords": "CoWoS、TSV"}]
    response = client.post("/api/tasks", json={"source_ids": [source_id], "start_date": "2026-08-01",
        "keyword_filter_enabled": True, "auto_structure_enabled": True, "keyword_config": config})
    assert response.status_code == 201
    assert response.json()["keyword_filter_enabled"] is True
    assert response.json()["auto_structure_enabled"] is True
    assert response.json()["keyword_config"] == config


def test_empty_csv_and_xlsx_have_formal_columns(client):
    expected_headers = [label for _, label in EXPORT_COLUMNS]
    csv_response = client.get("/api/exports?format=csv")
    assert csv_response.status_code == 200
    csv_text = csv_response.content.decode("utf-8-sig")
    assert csv_text.splitlines()[0].split(",") == expected_headers
    assert "资讯类型" in csv_text

    xlsx_response = client.get("/api/exports?format=xlsx")
    assert xlsx_response.status_code == 200
    workbook = load_workbook(BytesIO(xlsx_response.content))
    sheet = workbook["结构化结果"]
    assert [cell.value for cell in sheet[1]] == expected_headers
    assert sheet.freeze_panes == "A2"
    assert sheet.auto_filter.ref == "A1:J1"


def test_records_support_info_type_filter(client):
    response = client.get("/api/records?info_type=项目立项")
    assert response.status_code == 200
    assert response.json() == {"items": [], "total": 0}


def test_records_support_multiple_info_type_filters(client):
    from app.database import SessionLocal

    with SessionLocal() as db:
        db.add_all([
            StructuredRecord(region="华东", company_name="甲公司", info_type="项目立项", source_name="测试来源"),
            StructuredRecord(region="华南", company_name="乙公司", info_type="项目签约", source_name="测试来源"),
            StructuredRecord(region="华北", company_name="丙公司", info_type="项目竣工", source_name="测试来源"),
        ])
        db.commit()

    response = client.get("/api/records?info_type=项目立项&info_type=项目签约")
    assert response.status_code == 200
    assert response.json()["total"] == 2
    assert {item["info_type"] for item in response.json()["items"]} == {"项目立项", "项目签约"}


def test_analytics_overview_builds_keywords_and_relations(client):
    from app.database import SessionLocal

    with SessionLocal() as db:
        db.add_all([
            StructuredRecord(region="合肥", organization="高新区", company_name="长鑫存储",
                info_type="产能扩建", project_name="晶圆产线", details="DRAM 晶圆产能扩建",
                source_name="测试来源"),
            StructuredRecord(region="合肥", company_name="长鑫存储", info_type="研发进展",
                project_name="DRAM 芯片", details="DRAM 芯片研发取得进展", source_name="测试来源"),
        ])
        db.commit()

    client.put("/api/settings/model", json={"base_url": "https://api.example.com", "model_name": "test-model",
        "keyword_config": [{"industry": "存储", "field": "先进封装", "keywords": "DRAM、晶圆"}]})
    response = client.get("/api/analytics/overview?region=合肥")
    assert response.status_code == 200
    data = response.json()
    assert data["summary"]["record_count"] == 2
    assert data["summary"]["entity_count"] >= 4
    assert any(node["name"] == "长鑫存储" and node["value"] == 2 for node in data["graph"]["nodes"])
    assert any(edge["value"] >= 1 for edge in data["graph"]["edges"])
    assert any(item["name"] == "产能扩建" for item in data["info_types"])
    assert {item["text"] for item in data["keywords"]} == {"DRAM", "晶圆"}


def test_analytics_merges_same_entity_across_record_fields(client):
    from app.database import SessionLocal

    with SessionLocal() as db:
        db.add(StructuredRecord(region="海外", organization="Cerebras Systems",
            company_name="Cerebras Systems", info_type="经营动态", source_name="测试来源"))
        db.commit()

    response = client.get("/api/analytics/overview")
    assert response.status_code == 200
    nodes = [node for node in response.json()["graph"]["nodes"] if node["name"] == "Cerebras Systems"]
    assert len(nodes) == 1
    assert nodes[0]["category"] == "企业"


def test_history_full_text_search_and_manual_structure_guard(client):
    from app.database import SessionLocal
    from app.models import Source

    with SessionLocal() as db:
        source = db.query(Source).first()
        article = RawArticle(source_id=source.id, canonical_url="https://example.com/chip-project",
            title="晶圆厂扩建计划", published_text="2026年8月20日", body="华东晶圆制造项目新增产线",
            content_hash="a" * 64, status="pending")
        db.add(article); db.flush()
        db.add(StructuredRecord(article_id=article.id, region="华东", company_name="晶圆公司",
            info_type="产能扩建", investment_amount="未披露", project_name="新增产线",
            source_name=source.name, original_url=article.canonical_url, details="建设先进晶圆产线"))
        article_id = article.id
        db.commit()

    raw = client.get("/api/articles?q=新增产线")
    assert raw.status_code == 200
    assert raw.json()["total"] == 1
    assert raw.json()["items"][0]["record_count"] == 1

    records = client.get("/api/records?q=先进晶圆")
    assert records.status_code == 200
    assert records.json()["total"] == 1

    response = client.post(f"/api/articles/{article_id}/structure")
    assert response.status_code == 409
    assert "已经完成结构化" in response.json()["detail"]


def test_manual_structure_requires_configured_model(client):
    from app.database import SessionLocal
    from app.models import Source

    with SessionLocal() as db:
        source = db.query(Source).first()
        article = RawArticle(source_id=source.id, canonical_url="https://example.com/unstructured",
            title="待处理原文", body="正文", content_hash="b" * 64, status="pending")
        db.add(article); db.commit(); article_id = article.id

    response = client.post(f"/api/articles/{article_id}/structure")
    assert response.status_code == 409
    assert "API Key" in response.json()["detail"]


def test_manual_structure_ignores_auto_structure_switch(client, monkeypatch):
    from app.database import SessionLocal
    from app.models import Source

    setting = client.put("/api/settings/model", json={
        "base_url": "https://api.example.com", "model_name": "test-model",
        "api_key": "sk-secret1234", "enabled": False,
    })
    assert setting.status_code == 200

    with SessionLocal() as db:
        source = db.query(Source).first()
        article = RawArticle(source_id=source.id, canonical_url="https://example.com/manual",
            title="手动结构化原文", body="正文", content_hash="c" * 64, status="pending")
        db.add(article); db.commit(); article_id = article.id

    def fake_structure(db, article, setting):
        article.status = "completed"
        article.model_name = setting.model_name
        return 1

    monkeypatch.setattr("app.main.structure_article", fake_structure)
    response = client.post(f"/api/articles/{article_id}/structure")
    assert response.status_code == 200
    assert response.json() == {"article_id": article_id, "created_count": 1, "status": "completed"}


def test_model_setting_masks_secret(client):
    response = client.put("/api/settings/model", json={"base_url": "https://api.example.com", "model_name": "test-model", "api_key": "sk-secret1234", "enabled": True})
    assert response.status_code == 200
    assert response.json()["has_api_key"] is True
    assert response.json()["api_key_hint"].endswith("1234")
    assert "secret" not in response.text
    assert "api_key" not in client.get("/api/settings/model").json()


def test_source_rejects_cross_domain_entry(client):
    response = client.post("/api/sources", json={"name": "越界来源", "base_url": "https://example.com", "config": {
        "entry_urls": ["https://other.example/news"], "article_url_pattern": "/news/", "selectors": {"title": "h1", "date": ".date", "content": ".content"}}})
    assert response.status_code == 422


def test_audit_export_has_trace_columns(client):
    response = client.get("/api/exports?format=xlsx&columns=audit")
    workbook = load_workbook(BytesIO(response.content))
    headers = [cell.value for cell in workbook["结构化结果"][1]]
    assert "字段证据" in headers
    assert "任务ID" in headers
