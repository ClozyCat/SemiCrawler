DEFAULT_START_DATE = "2026-08-01"

INFO_TYPES = [
    "项目立项", "项目签约", "建设开工", "工程建设", "设备进场", "项目竣工",
    "试生产/投产", "产能扩建", "融资/投资", "并购重组", "产品发布", "研发进展",
    "技术突破", "产学研合作", "政策/规划", "招商引资", "人才/实验室",
    "供应链合作", "经营动态", "风险/事故", "其他",
]

# The region value is deliberately a small, stable vocabulary so that filters and
# exports remain comparable across different model providers.
REGION_OPTIONS = [
    "中国大陆-华北", "中国大陆-东北", "中国大陆-华东", "中国大陆-华中",
    "中国大陆-华南", "中国大陆-西南", "中国大陆-西北", "中国台湾", "中国香港",
    "中国澳门", "海外", "其他",
]

EXPORT_COLUMNS = [
    ("region", "地域"), ("organization", "开发区/院校"), ("company_name", "企业名称"),
    ("event_date", "日期"), ("info_type", "资讯类型"),
    ("investment_amount", "投资金额"), ("project_name", "产品/项目名称"),
    ("source_name", "信息来源"), ("original_url", "网址/原文"), ("details", "详细信息"),
]
