"""Deprecated compatibility models; runtime integration is implemented by app.tavily."""
from pydantic import BaseModel, Field, HttpUrl


class DokobotError(ValueError):
    pass


class DokobotSearchItem(BaseModel):
    title: str = "网页结果"
    link: HttpUrl
    snippet: str = ""


class DokobotPage(BaseModel):
    title: str = ""
    url: HttpUrl
    text: str = Field(min_length=1)
    session_id: str | None = None
