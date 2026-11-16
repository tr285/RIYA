from core.agent.assistant import RIYAAssistant
from core.config.settings import (
    APP_NAME,
    APP_VERSION,
    DEVELOPER_NAME,
)


def main():
    assistant = RIYAAssistant()

    print("=" * 50)
    print(f"{APP_NAME} {APP_VERSION}")
    print(f"Developer: {DEVELOPER_NAME}")
    print("=" * 50)
    print("RIYA is ready. Type 'exit' to quit.\n")

    try:
        while True:
            user = input("You: ").strip()

            if not user:
                continue

            response = assistant.process(user)

            print(f"RIYA: {response}")

            if user.lower() in {"exit", "quit", "stop"}:
                break

    finally:
        assistant.close()


if __name__ == "__main__":
    main()