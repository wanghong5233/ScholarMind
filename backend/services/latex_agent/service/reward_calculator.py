"""
奖励计算模块
用于 RL 训练时的奖励计算
"""
from typing import Dict, Any, Optional
from dataclasses import dataclass
import logging

logger = logging.getLogger(__name__)


@dataclass
class RewardWeights:
    """奖励权重配置"""
    task: float = 1.0
    efficiency: float = 0.3
    quality: float = 0.5
    error_fix: float = 0.4
    cost: float = 0.1


class RewardCalculator:
    """
    奖励计算器
    计算 Agent 执行动作的奖励信号
    """
    
    def __init__(self, weights: Optional[RewardWeights] = None):
        """
        初始化奖励计算器
        
        Args:
            weights: 奖励权重配置
        """
        self.weights = weights or RewardWeights()
    
    def calculate_reward(
        self,
        step: Dict[str, Any],
        state_before: Dict[str, Any],
        state_after: Dict[str, Any],
        user_intent: str,
        task_completed: bool
    ) -> float:
        """
        计算单个步骤的奖励
        
        Args:
            step: Agent 执行步骤
            state_before: 执行前状态
            state_after: 执行后状态
            user_intent: 用户意图
            task_completed: 任务是否完成
            
        Returns:
            奖励值
        """
        reward = 0.0
        
        # 1. 任务完成奖励
        reward += self.weights.task * self._reward_task_completion(
            step, state_after, user_intent, task_completed
        )
        
        # 2. 效率奖励
        reward += self.weights.efficiency * self._reward_efficiency(
            step, state_before, state_after
        )
        
        # 3. 质量奖励
        reward += self.weights.quality * self._reward_quality(
            step, state_after
        )
        
        # 4. 错误修复奖励
        reward += self.weights.error_fix * self._reward_error_fix(
            step, state_before, state_after
        )
        
        # 5. 成本惩罚
        reward += self.weights.cost * self._reward_cost(step)
        
        return reward
    
    def _reward_task_completion(
        self,
        step: Dict[str, Any],
        state_after: Dict[str, Any],
        user_intent: str,
        task_completed: bool
    ) -> float:
        """任务完成奖励"""
        if step.get("type") == "finish":
            if task_completed:
                return 10.0
            else:
                return -5.0
        
        # 部分完成奖励
        if step.get("type") == "result":
            result = step.get("result", {})
            if result.get("success"):
                # 根据工具执行结果评估完成度
                data = result.get("data", {})
                
                # 计算完成度（0-1）
                completion_rate = self._calculate_completion_rate(data, state_after, user_intent)
                
                if completion_rate > 0:
                    return 5.0 * completion_rate
        
        return 0.0
    
    def _calculate_completion_rate(
        self,
        data: Dict[str, Any],
        state_after: Dict[str, Any],
        user_intent: str
    ) -> float:
        """
        计算任务完成度（0-1）
        
        Args:
            data: 工具执行结果数据
            state_after: 执行后状态
            user_intent: 用户意图
            
        Returns:
            完成度（0-1）
        """
        # 根据用户意图和实际结果计算完成度
        user_intent_lower = user_intent.lower()
        
        # 添加引用任务
        if "添加引用" in user_intent or "add citation" in user_intent_lower:
            if "changes" in data and len(data["changes"]) > 0:
                # 假设每个 change 代表一个引用，3个引用为完成
                return min(1.0, len(data["changes"]) / 3.0)
            if "citation_key" in data:
                return 0.33  # 添加了一个引用
        
        # 批量添加引用任务
        if "批量添加" in user_intent or "batch add" in user_intent_lower:
            if "changes" in data:
                # 假设5个引用为完成
                return min(1.0, len(data["changes"]) / 5.0)
        
        # 检查引用任务
        if "检查" in user_intent or "check" in user_intent_lower:
            if "issues" in data or "inconsistent_citations" in data:
                return 0.5  # 检查完成
        
        # 编译任务
        if "编译" in user_intent or "compile" in user_intent_lower:
            if data.get("compiled"):
                return 1.0  # 编译成功
        
        # 如果有变更，说明有进展
        if "changes" in data and len(data["changes"]) > 0:
            return 0.2  # 有进展但不确定完成度
        
        return 0.0
    
    def _reward_efficiency(
        self,
        step: Dict[str, Any],
        state_before: Dict[str, Any],
        state_after: Dict[str, Any]
    ) -> float:
        """效率奖励"""
        reward = 0.0
        
        # 并行调用奖励（如果工具支持并行）
        if step.get("type") == "action":
            tool_name = step.get("tool_name")
            # 检查是否是并行调用（需要从参数中判断）
            # 这里简化处理
            if tool_name == "batch_search_papers_tool":
                reward += 2.0
        
        # 检查迭代次数变化（效率奖励）
        iterations_before = state_before.get("execution_history_length", 0)
        iterations_after = state_after.get("execution_history_length", 0)
        
        # 如果迭代次数增加较少（<=1），说明效率高
        if iterations_after - iterations_before <= 1:
            reward += 1.0
        
        return reward
    
    def _reward_quality(
        self,
        step: Dict[str, Any],
        state_after: Dict[str, Any]
    ) -> float:
        """质量奖励"""
        reward = 0.0
        
        if step.get("type") == "result":
            result = step.get("result", {})
            if result.get("success"):
                data = result.get("data", {})
                
                # 编译成功奖励
                if step.get("tool_name") == "compile_latex_tool":
                    if data.get("compiled"):
                        reward += 5.0
                
                # 引用格式正确奖励
                if step.get("tool_name") == "insert_citation_tool":
                    # 检查引用格式
                    if "citation_key" in data:
                        reward += 2.0
        
        return reward
    
    def _reward_error_fix(
        self,
        step: Dict[str, Any],
        state_before: Dict[str, Any],
        state_after: Dict[str, Any]
    ) -> float:
        """错误修复奖励"""
        reward = 0.0
        
        tool_name = step.get("tool_name", "")
        
        # 检测错误奖励
        if tool_name in ["check_citation_consistency_tool", "check_bibliography_tool"]:
            result = step.get("result", {})
            if result.get("success"):
                data = result.get("data", {})
                # 统计检测到的错误数
                errors_found = 0
                if "inconsistent_citations" in data:
                    errors_found += len(data["inconsistent_citations"])
                if "missing_citations" in data:
                    errors_found += len(data["missing_citations"])
                reward += 2.0 * errors_found
        
        # 修复错误奖励
        if tool_name in ["fix_citation_format_tool", "fix_bibtex_tool"]:
            result = step.get("result", {})
            if result.get("success"):
                # 检查修复前后的错误数变化
                errors_before = self._count_errors(state_before)
                errors_after = self._count_errors(state_after)
                errors_fixed = errors_before - errors_after
                reward += 5.0 * errors_fixed
        
        return reward
    
    def _reward_cost(self, step: Dict[str, Any]) -> float:
        """成本惩罚（返回负值）"""
        cost = 0.0
        
        # LLM 调用成本
        if step.get("type") in ["action", "thought"]:
            cost += 0.1
        
        # 工具调用成本
        if step.get("type") == "action":
            cost += 0.05
        
        # 编译成本
        if step.get("tool_name") == "compile_latex_tool":
            cost += 0.2
        
        return -cost
    
    def _count_errors(self, state: Dict[str, Any]) -> int:
        """统计状态中的错误数"""
        errors = 0
        
        # 从执行历史中提取错误信息
        # 注意：state 字典中可能包含 execution_history_length，但不包含完整的执行历史
        # 这里简化处理，实际应该从完整的执行历史中提取
        
        # 如果状态中包含错误信息，直接统计
        if "errors" in state:
            errors += len(state["errors"]) if isinstance(state["errors"], list) else 1
        
        # 如果状态中包含编译错误信息
        if "compilation_errors" in state:
            errors += len(state["compilation_errors"]) if isinstance(state["compilation_errors"], list) else 1
        
        # 如果状态中包含引用错误信息
        if "inconsistent_citations" in state:
            errors += len(state["inconsistent_citations"]) if isinstance(state["inconsistent_citations"], list) else 1
        
        if "missing_citations" in state:
            errors += len(state["missing_citations"]) if isinstance(state["missing_citations"], list) else 1
        
        return errors

