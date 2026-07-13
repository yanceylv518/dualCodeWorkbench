from .models import HandoffPackage


def handoff_prompt(package: HandoffPackage) -> str:
    payload = package.payload
    if package.recipient == "claude":
        instruction = (
            "Perform an independent product-grade review of this structured handoff. "
            "Do not assume access to files not included. Return sections: verdict, completed, "
            "missing or not implemented, partial implementation, potential problems, regression "
            "risks, architecture or temporary-solution violations, evidence gaps, and required "
            "actions. Explicitly inspect whether Codex ignored requirements or used demo-style, "
            "hard-coded, simulated, bypass, or unsustainable framework choices."
        )
    else:
        instruction = (
            "Validate this plan or review package against the real local repository. Distinguish "
            "confirmed repository facts from suggestions. Report conflicts, missing requirements, "
            "affected files, formal architecture changes needed, tests required, and implementation "
            "readiness. Do not implement a temporary or demo-style workaround."
        )
    return f"{instruction}\n\nSTRUCTURED HANDOFF PACKAGE:\n{payload}"
