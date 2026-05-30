# train_mock.py
import time
import sys

print("🚀 [train_mock.py] 开始模拟模型训练...")

for i in range(1, 6):
    print(f"   -> Epoch {i}/5 正在训练，Loss: {0.5 / i:.4f}...")
    time.sleep(1)  # 每次 Epoch 模拟耗时 1 秒

print("✅ [train_mock.py] 训练完美收官！模型参数已保存至 'mock_model.pth'。")
sys.exit(0)  # 返回 0 表示成功