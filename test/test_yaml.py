import yaml

with open('xncagent/config/prompts/xiaoxizi_system.yaml', 'r') as file:
    data = yaml.load(file, Loader=yaml.FullLoader)

print(data)