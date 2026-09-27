from pathlib import Path

import yaml

if __name__ == "__main__":
    path = Path(__file__).resolve().parents[1] / "xncagent/config/prompts/xiaoxizi_system.yaml"
    with path.open(encoding="utf-8") as file:
        data = yaml.load(file, Loader=yaml.FullLoader)
    print(data)
