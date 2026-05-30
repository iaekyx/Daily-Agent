import asyncio
from scheduler import AgentScheduler

async def main():
    # 实例化你的智能调度器
    scheduler = AgentScheduler()

    # ==========================================
    # 1. 你告诉 Agent：“帮我按顺序跑这三个脚本”
    # ==========================================
    # 注意：这里的 command 我用 sleep 模拟了模型训练的耗时
    
    scheduler.add_task(
        task_id="train_v1", 
        command="echo '开始训练第一版模型...' && sleep 5 && echo '模型 1 保存成功！'"
    )
    
    scheduler.add_task(
        task_id="test_model", 
        command="echo '载入模型 1 进行评测...' && sleep 3 && echo '测试通过，精度 85%'", 
        depends_on=["train_v1"]  # 🚨 魔法在这里：它会一直等 train_v1 结束！
    )
    
    scheduler.add_task(
        task_id="train_v2", 
        command="echo '基于评测结果微调，开始训练修改版...' && sleep 4 && echo '终极模型完成！'", 
        depends_on=["test_model"] # 🚨 只有测试通过了，才会跑修改版
    )

    # ==========================================
    # 2. 你去睡觉了，Agent 开始接管后台
    # ==========================================
    print("\n🌙 你可以去睡觉了，Agent 开始在后台干活...")
    
    # 启动流水线
    await scheduler.start_pipeline()

# 运行
if __name__ == "__main__":
    asyncio.run(main())