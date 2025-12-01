import yaml
import redis

# 1. 读取本地 routes.yaml
with open("gateway/routes.yaml", "r") as f:
    routes = yaml.safe_load(f) or {}

print("Loaded from YAML:", routes)

# 2. 写入 Redis
r = redis.Redis(host="localhost", port=6379, decode_responses=True)

for key, value in routes.items():
    r.hset("routes", key, value)

print("Successfully imported into Redis!")
