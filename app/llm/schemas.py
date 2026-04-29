from pydantic import BaseModel, Field, model_validator


class PainPointDraft(BaseModel):
    has_unmet_need: bool
    problem_text: str | None = None
    audience: str | None = None
    urgency_cue: str | None = None
    current_workaround: str | None = None

    @model_validator(mode="after")
    def _coherent(self) -> "PainPointDraft":
        if self.has_unmet_need and not (self.problem_text and self.audience):
            raise ValueError("problem_text and audience required when has_unmet_need is True")
        return self


class ClusterLabel(BaseModel):
    problem_statement: str
    audience: str
    why_now: str
    specificity: int = Field(ge=1, le=5)
    suggested_category_slug: str | None = None
