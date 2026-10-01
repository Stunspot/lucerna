"""Original, offline teaching laboratories included with Lucerna."""
from pathlib import Path
import hashlib
import json
from engine.storyboards import validate_storyboard

ROOT = Path(__file__).resolve().parent
LABS = [
    {"id":"attention","file":"attention-lab","title":"Inside attention","description":"Point a query, watch the mixture change, and try to smuggle information through a causal mask.","scene_count":5},
    {"id":"memory","file":"memory-lab","title":"When memory loses the meaning","description":"Keep the facts, lose the plot. Explore why temporal context and the consuming model change what memory is worth.","scene_count":3},
    {"id":"equilibrium","file":"dynamic-equilibrium","title":"Stillness that keeps moving","description":"Change reaction rates and discover what chemical equilibrium actually holds constant.","scene_count":1},
    {"id":"oscillator","file":"spring-mass","title":"The shape of a vibration","description":"Turn mass, stiffness and damping into a motion you can inspect.","scene_count":1},
]

def available_showcases():
    return [{key: value for key, value in lab.items() if key != "file"} for lab in LABS
            if (ROOT/"examples"/(lab["file"]+".json")).is_file()]

def install_showcase(workspace, slug, learner_id="default"):
    from workspace import AppError
    workspace.learning.get_learner(learner_id)
    lab=next((lab for lab in LABS if lab["id"]==slug),None)
    if lab is None:
        raise AppError("not_found","That teaching lab is not included.")
    document=json.loads((ROOT/"examples"/(lab["file"]+".document.json")).read_text(encoding="utf-8"))
    board=json.loads((ROOT/"examples"/(lab["file"]+".json")).read_text(encoding="utf-8"))
    # A companion is authored material; it must not occupy a raw import's hash ID.
    if not document["id"].startswith("lesson-"):
        document["id"]="lesson-lab-"+slug
        board["document_id"]=document["id"]
    board=validate_storyboard(board,document)
    # The source copy is an original companion lesson, never the cited full paper.
    markdown="# "+document["title"]+"\n\n"+"\n\n".join(
        "## "+section["title"]+"\n\n"+"\n\n".join(block.get("text","") for block in section["blocks"])
        for section in document["sections"])
    raw=markdown.encode("utf-8")
    document["source"]={**document.get("source",{}),"kind":"authored","format":"markdown",
        "original":"Original Lucerna teaching laboratory","filename":lab["file"]+".md",
        "sha256":hashlib.sha256(raw).hexdigest(),"cache_path":document["id"]+"/source.md"}
    try:
        existing=workspace.get_document(document["id"])
    except AppError as error:
        if error.code!="not_found":
            raise
        existing=None
    if existing is None:
        target=(workspace.home/"papers"/document["id"]).resolve()
        if not target.is_relative_to((workspace.home/"papers").resolve()):
            raise AppError("invalid","Invalid laboratory source identity.")
        target.mkdir(parents=True,exist_ok=True)
        source=target/"source.md"
        if source.exists() and source.read_bytes()!=raw:
            raise AppError("conflict","A different source already occupies this laboratory cache.")
        source.write_bytes(raw)
        (target/"document.json").write_text(json.dumps(document,ensure_ascii=False),encoding="utf-8")
        workspace.save_document(document,kind="lesson")
    if workspace.get_storyboard(document["id"]) is None:
        workspace.save_storyboard(document["id"],board,expected_revision=0)
    # Companion connections describe the lesson, never the learner's competence.
    if existing is None:
        for first, second in zip(document["sections"], document["sections"][1:]):
            if not first["blocks"] or not second["blocks"]:
                continue
            workspace.save_connection(document["id"], {
                "from":{"label":first["title"],"block_id":first["blocks"][0]["id"]},
                "to":{"label":second["title"],"block_id":second["blocks"][0]["id"]},
                "relation":"related",
                "explanation":"Adjacent concepts in this authored laboratory. The source passages explain the relationship; this link asserts no prerequisite or learner mastery.",
                "provenance":{"basis":"agent-authored","author":"Lucerna companion lesson"}
            },learner_id=learner_id)
    return {"document_id":document["id"],"title":document["title"],"installed":existing is None,
            "scene_count":len(board["scenes"]),"learner_id":learner_id}
