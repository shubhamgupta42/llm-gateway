from pydantic import BaseModel, Field


class Message(BaseModel):
    role: str = Field(pattern="^(system|user|assistant)$")
    content: str = Field(max_length=100_000)


class ChatRequest(BaseModel):
    model: str
    messages: list[Message] = Field(min_length=1)


class ChatResponse(BaseModel):
    id: int
    model: str
    content: str
    usage: dict         # normalized token categories
    raw_usage: dict     # the provider's own schema, for transparency
    cost: dict          # rating output: breakdown / units / capacity_units


class UsageSummary(BaseModel):
    tenant: str
    by_model: dict[str, dict]  # model -> {calls, tokens..., capacity_units}
    total_capacity_units: str


class BillingRunResult(BaseModel):
    processed_records: int
    billed_rows: int
    window_start: str | None
    window_end: str


class VerifyReport(BaseModel):
    checked: int
    mismatches: list[dict]
    passed: bool


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int  # seconds until expiry
