"""AI helpers: classify a patient description and summarize a long case."""
from hospital_core import validate as v
from hospital_core.router import route
from hospital_core.services import ai_tools


@route("POST", "/ai/classify", roles=("patient", "coordinator", "admin"))
def classify(ctx):
    return ai_tools.classify(v.text(ctx.body, "text", 3, 5000))


@route("POST", "/ai/summarize", roles=("patient", "coordinator", "admin"))
def summarize(ctx):
    return ai_tools.summarize(v.text(ctx.body, "text", 3, 5000))
