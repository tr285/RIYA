from core.agent.agent import RiyaAgent
from core.ai.local_runtime import LocalLlamaRuntime


MODEL_PATH = (
    "~/.cache/huggingface/hub/models--lmstudio-community--"
    "Qwen3-4B-Instruct-2507-GGUF/blobs/"
    "8cdb57cbb880d313736a9bc4e3d3d2485f145b5e19cf33783746e753e82641fc"
)


def main():
    agent = RiyaAgent()

    ai = LocalLlamaRuntime()
    ai.load_model(MODEL_PATH)

    try:
        plan = agent.create_ai_plan(
            "Set the volume to 40 percent and take a screenshot",
            ai,
        )

        print("\nRIYA PLAN")
        print("---------")

        for index, step in enumerate(plan.steps, start=1):
            print(
                f"{index}. "
                f"{step.action} "
                f"{step.parameters}"
            )

        print("\nRIYA EXECUTION")
        print("--------------")

        results = []

        for step in plan.steps:
            result = agent.execute(
                step.action,
                **step.parameters,
            )

            results.append(result)
            print(result)

    finally:
        ai.unload_model()


if __name__ == "__main__":
    main()