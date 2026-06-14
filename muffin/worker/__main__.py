"""Entry point: python -m muffin.worker"""

from .agent import Agent


def main() -> None:
    Agent().run_forever()


if __name__ == "__main__":
    main()
