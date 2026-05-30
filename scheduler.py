import asyncio
import datetime
from enum import Enum
from pathlib import Path

class Status(Enum):
    PENDING = "待执行"
    RUNNING = "运行中"
    COMPLETED = "✅ 成功"
    FAILED = "❌ 失败"

class Task:
    def __init__(self, task_id, command, depends_on=None):
        self.task_id = task_id           # 任务名称，如 "train_v1"
        self.command = command           # 终端命令，如 "python train.py"
        self.depends_on = depends_on or [] # 依赖的任务 ID 列表
        self.status = Status.PENDING
        self.started_at = None
        self.ended_at = None
        self.returncode = None
        self.output = ""
        self.error = ""

    def snapshot(self):
        return {
            "task_id": self.task_id,
            "command": self.command,
            "depends_on": self.depends_on,
            "status": self.status.value,
            "started_at": self.started_at,
            "ended_at": self.ended_at,
            "returncode": self.returncode,
            "output": self.output[-4000:],
            "error": self.error,
        }

class AgentScheduler:
    def __init__(self, work_dir=None, on_update=None):
        self.tasks = {}
        self.work_dir = work_dir # 支持从主程序传入工作目录
        self.on_update = on_update

    def _now(self):
        return datetime.datetime.now().isoformat(timespec="seconds")

    def _notify(self):
        if self.on_update:
            self.on_update(self.snapshot())

    def snapshot(self):
        return {
            "tasks": [task.snapshot() for task in self.tasks.values()]
        }

    def add_task(self, task_id, command, depends_on=None):
        """向队列中添加任务"""
        self.tasks[task_id] = Task(task_id, command, depends_on)
        print(f"📦 [队列] 任务已登记: {task_id} (依赖: {depends_on or '无'})")
        self._notify()

    async def _run_single_task(self, task: Task):
        """运行单个任务的逻辑，包含依赖等待机制"""
        # 1. 检查依赖（阻塞等待直到前置任务完成）
        for dep_id in task.depends_on:
            dep_task = self.tasks.get(dep_id)
            if not dep_task:
                print(f"⚠️ [警告] 任务 {task.task_id} 的依赖 {dep_id} 不存在！取消执行。")
                task.status = Status.FAILED
                task.error = f"Missing dependency: {dep_id}"
                task.ended_at = self._now()
                self._notify()
                return
            
            # 死循环等待前置任务脱离 PENDING 或 RUNNING 状态
            while dep_task.status in (Status.PENDING, Status.RUNNING):
                await asyncio.sleep(2) # 每 2 秒检查一次
                
            # 如果前置任务失败了，后续任务直接取消
            if dep_task.status == Status.FAILED:
                print(f"🛑 [中断] 因为 {dep_id} 失败，级联取消任务: {task.task_id}")
                task.status = Status.FAILED
                task.error = f"Dependency failed: {dep_id}"
                task.ended_at = self._now()
                self._notify()
                return

        # 2. 执行本任务
        task.status = Status.RUNNING
        task.started_at = self._now()
        self._notify()
        print(f"\n🚀 [启动] {task.task_id} -> 执行指令: `{task.command}`")
        
        # 使用异步子进程在后台静默运行，并指定执行目录
        process = await asyncio.create_subprocess_shell(
            task.command, 
            cwd=self.work_dir,
            stdin=asyncio.subprocess.DEVNULL,  # 🚨 核心修复：将后台进程的输入重定向到黑洞，防止劫持键盘！
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        stdout, _ = await process.communicate() # 等待进程结束
        task.returncode = process.returncode
        if stdout:
            task.output = stdout.decode(errors="replace")
        task.ended_at = self._now()
        
        # 3. 记录结果
        if process.returncode == 0:
            task.status = Status.COMPLETED
            print(f"✅ [完成] {task.task_id} 顺利结束！")
        else:
            task.status = Status.FAILED
            task.error = f"Exit code: {process.returncode}"
            print(f"❌ [崩溃] {task.task_id} 运行报错，退出码: {process.returncode}")
        self._notify()

    async def start_pipeline(self):
        """启动流水线（非阻塞）"""
        print("\n⚙️  [引擎] 启动自动化任务流水线...")
        # 将所有状态为 PENDING 的任务丢进异步池里并发执行
        coroutines = [self._run_single_task(t) for t in self.tasks.values() if t.status == Status.PENDING]
        if coroutines:
            await asyncio.gather(*coroutines)
        print("\n🏁 [引擎] 所有后台队列任务已处理完毕！")
        self._notify()
