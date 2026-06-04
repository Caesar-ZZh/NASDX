"""
NASDX V2 — 强化学习策略引擎
参考 FinRL 架构，基于 stable-baselines3
支持 PPO / A2C / DDPG 算法训练 ETF 交易策略
"""
from __future__ import annotations
import numpy as np
import pandas as pd
from typing import Optional, Tuple
from pathlib import Path

ROOT = Path(__file__).parent.parent
MODEL_DIR = ROOT / "models"
MODEL_DIR.mkdir(exist_ok=True)


# ══════════════════════════════════════════
#  交易环境（Gymnasium 兼容）
# ══════════════════════════════════════════
class ETFTradingEnv:
    """
    ETF 交易强化学习环境
    参考 FinRL StockTradingEnv 设计
    state:  [现金比例, 各ETF持仓比例, 各ETF技术指标...]
    action: 各ETF目标仓位比例（连续动作空间）
    reward: 日收益率 - 手续费惩罚
    """

    def __init__(
        self,
        price_data:      dict[str, pd.DataFrame],
        initial_capital: float = 100_000,
        commission:      float = 0.0003,
        window:          int   = 20,         # 历史窗口
        max_pos:         float = 0.4,        # 单只最大仓位
    ):
        self.price_data      = price_data
        self.codes           = list(price_data.keys())
        self.n_stocks        = len(self.codes)
        self.initial_capital = initial_capital
        self.commission      = commission
        self.window          = window
        self.max_pos         = max_pos

        # 对齐日期索引
        self._build_price_matrix()

        # 状态维度：[现金] + [n_stocks持仓] + [n_stocks * 5个指标]
        self.state_dim  = 1 + self.n_stocks + self.n_stocks * 5
        self.action_dim = self.n_stocks  # 每只ETF的目标权重

    def _build_price_matrix(self):
        """构建价格矩阵"""
        dfs = []
        for code in self.codes:
            df = self.price_data[code][["close","volume"]].copy()
            df.columns = [f"{code}_close", f"{code}_vol"]
            dfs.append(df)
        self.price_matrix = pd.concat(dfs, axis=1).dropna()
        self.dates = self.price_matrix.index
        self.T = len(self.dates)

    def reset(self) -> np.ndarray:
        self.t       = self.window
        self.capital = self.initial_capital
        self.weights = np.zeros(self.n_stocks)  # 各ETF持仓权重
        return self._get_state()

    def step(self, action: np.ndarray) -> Tuple[np.ndarray, float, bool, dict]:
        """执行一步"""
        # 归一化动作到目标权重（softmax确保>0，且限制单只上限）
        action = np.clip(action, 0, self.max_pos)
        total  = action.sum()
        if total > 1.0:
            action /= total
        target_weights = action

        # 计算当日收益
        date_prev  = self.dates[self.t - 1]
        date_today = self.dates[self.t]

        returns = np.array([
            self.price_matrix.loc[date_today, f"{c}_close"] /
            self.price_matrix.loc[date_prev,  f"{c}_close"] - 1
            for c in self.codes
        ])

        # 调仓手续费
        turnover   = np.abs(target_weights - self.weights).sum()
        trade_cost = turnover * self.commission

        # 组合收益
        port_ret = np.dot(self.weights, returns) - trade_cost

        # 更新状态
        self.weights = target_weights.copy()
        self.capital *= (1 + port_ret)
        self.t += 1

        reward = port_ret * 100  # 放大 reward 信号
        done   = self.t >= self.T - 1

        return self._get_state(), reward, done, {
            "date": str(date_today),
            "portfolio_return": port_ret,
            "capital": self.capital,
        }

    def _get_state(self) -> np.ndarray:
        """构建状态向量"""
        cash_ratio = 1.0 - self.weights.sum()
        state = [cash_ratio] + list(self.weights)

        # 添加技术指标（近window日的归一化指标）
        for code in self.codes:
            col = f"{code}_close"
            prices = self.price_matrix[col].iloc[max(0, self.t - self.window):self.t]
            if len(prices) < 5:
                state.extend([0] * 5)
                continue
            p = prices.values
            roc5  = (p[-1] / p[-5] - 1) if len(p) >= 5 else 0
            roc20 = (p[-1] / p[0]  - 1)
            ma5   = p[-5:].mean() / p[-1] - 1
            std10 = p[-min(10, len(p)):].std() / (p[-1] + 1e-9)
            vol_col = f"{code}_vol"
            vols  = self.price_matrix[vol_col].iloc[max(0, self.t-self.window):self.t].values
            vratio = vols[-1] / (vols[:-1].mean() + 1e-9) if len(vols) > 1 else 1.0
            state.extend([roc5, roc20, ma5, std10, vratio])

        return np.array(state, dtype=np.float32)


# ══════════════════════════════════════════
#  RL 训练器
# ══════════════════════════════════════════
class RLTrainer:
    """
    强化学习策略训练器
    参考 FinRL DRLAgent 设计
    """

    ALGORITHMS = ["PPO", "A2C", "DDPG", "TD3", "SAC"]

    def __init__(self, algorithm: str = "PPO"):
        self.algorithm = algorithm.upper()
        self._model    = None

    def train(
        self,
        env: ETFTradingEnv,
        total_timesteps: int = 50_000,
        verbose: int = 1,
    ):
        """训练策略"""
        try:
            import gymnasium as gym
            from stable_baselines3 import PPO, A2C, DDPG, TD3, SAC
            from stable_baselines3.common.env_util import make_vec_env
        except ImportError:
            raise RuntimeError("请安装：pip install stable-baselines3 gymnasium")

        # 包装成 gymnasium 环境
        gym_env = _GymWrapper(env)

        algo_map = {"PPO": PPO, "A2C": A2C, "DDPG": DDPG, "TD3": TD3, "SAC": SAC}
        AlgoCls = algo_map.get(self.algorithm, PPO)

        policy = "MlpPolicy"
        if self.algorithm in ("DDPG", "TD3", "SAC"):
            self._model = AlgoCls(policy, gym_env, verbose=verbose)
        else:
            self._model = AlgoCls(policy, gym_env, verbose=verbose)

        print(f"\n🤖 开始训练 {self.algorithm} 策略...")
        print(f"   状态维度: {env.state_dim}  动作维度: {env.action_dim}")
        print(f"   训练步数: {total_timesteps:,}")
        self._model.learn(total_timesteps=total_timesteps, progress_bar=(verbose > 0))
        print(f"✅ 训练完成！")
        return self

    def save(self, name: str = "rl_model"):
        if self._model is None:
            raise RuntimeError("请先训练模型")
        path = str(MODEL_DIR / f"{name}_{self.algorithm}")
        self._model.save(path)
        print(f"模型已保存：{path}")
        return path

    def load(self, path: str):
        try:
            from stable_baselines3 import PPO, A2C, DDPG, TD3, SAC
            algo_map = {"PPO": PPO, "A2C": A2C, "DDPG": DDPG, "TD3": TD3, "SAC": SAC}
            AlgoCls = algo_map.get(self.algorithm, PPO)
            self._model = AlgoCls.load(path)
        except ImportError:
            raise RuntimeError("请安装 stable-baselines3")
        return self

    def predict(self, state: np.ndarray) -> np.ndarray:
        """预测动作（目标权重）"""
        if self._model is None:
            raise RuntimeError("模型未加载")
        action, _ = self._model.predict(state, deterministic=True)
        # 归一化为权重
        action = np.clip(action, 0, None)
        total  = action.sum()
        return action / total if total > 1e-9 else action

    def backtest(self, env: ETFTradingEnv) -> dict:
        """用训练好的模型跑回测"""
        state = env.reset()
        total_reward = 0
        done = False
        capital_history = [env.capital]
        while not done:
            action = self.predict(state)
            state, reward, done, info = env.step(action)
            total_reward += reward
            capital_history.append(info["capital"])

        equity = pd.Series(capital_history)
        total_ret = equity.iloc[-1] / equity.iloc[0] - 1
        daily_ret = equity.pct_change().dropna()
        sharpe = daily_ret.mean() / (daily_ret.std() + 1e-9) * np.sqrt(252)
        dd = ((equity - equity.cummax()) / equity.cummax()).min()

        return {
            "total_return":   total_ret,
            "annual_return":  (1 + total_ret) ** (252 / len(equity)) - 1,
            "sharpe_ratio":   sharpe,
            "max_drawdown":   dd,
            "total_reward":   total_reward,
            "equity_curve":   equity,
        }


class _GymWrapper:
    """将 ETFTradingEnv 包装为 gymnasium.Env"""
    def __init__(self, env: ETFTradingEnv):
        self.env = env
        try:
            import gymnasium as gym
            self.observation_space = gym.spaces.Box(
                low=-np.inf, high=np.inf,
                shape=(env.state_dim,), dtype=np.float32
            )
            self.action_space = gym.spaces.Box(
                low=0.0, high=1.0,
                shape=(env.action_dim,), dtype=np.float32
            )
        except ImportError:
            pass

    def reset(self, **kwargs):
        obs = self.env.reset()
        return obs, {}

    def step(self, action):
        obs, reward, done, info = self.env.step(action)
        return obs, reward, done, False, info

    def render(self): pass
    def close(self):  pass
