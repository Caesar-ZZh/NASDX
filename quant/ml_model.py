"""
NASDX V2 — 机器学习预测模型
借鉴 QLib Model Zoo，实现 LightGBM + Ridge + 集成预测

抗过拟合措施：
  - LightGBM: 早停 + 最大深度限制 + 特征采样
  - Ridge 回归: L2 正则化，天然抗过拟合
  - 集成：多模型平均，降低方差
  - Walk-Forward 训练：每次只用历史数据训练
"""
from __future__ import annotations
import numpy as np
import pandas as pd
from typing import Optional, Tuple
from pathlib import Path

MODEL_DIR = Path(__file__).parent.parent / "models"
MODEL_DIR.mkdir(exist_ok=True)


# ══════════════════════════════════════════
#  基础预测器
# ══════════════════════════════════════════
class RidgePredictor:
    """
    岭回归预测器（最简单、最不容易过拟合）
    参考 QLib 的线性 alpha 因子模型
    """

    def __init__(self, alpha: float = 1.0):
        self.alpha = alpha
        self._coef = None
        self._mean = None
        self._std  = None

    def fit(self, X: pd.DataFrame, y: pd.Series) -> "RidgePredictor":
        from sklearn.linear_model import Ridge
        from sklearn.preprocessing import StandardScaler

        Xv = X.values; yv = y.values
        self._mean = Xv.mean(axis=0)
        self._std  = Xv.std(axis=0) + 1e-9
        Xn = (Xv - self._mean) / self._std

        model = Ridge(alpha=self.alpha, fit_intercept=True)
        model.fit(Xn, yv)
        self._coef  = model.coef_
        self._inter = model.intercept_
        return self

    def predict(self, X: pd.DataFrame) -> np.ndarray:
        Xv = X.values
        Xn = (Xv - self._mean) / self._std
        return Xn @ self._coef + self._inter


class LGBMPredictor:
    """
    LightGBM 预测器
    参考 QLib workflow_config_lightgbm_Alpha158.yaml 配置
    """

    def __init__(
        self,
        n_estimators:    int   = 200,
        max_depth:       int   = 5,       # 限制深度防止过拟合
        learning_rate:   float = 0.05,
        feature_fraction:float = 0.7,     # 特征采样
        subsample:       float = 0.8,     # 样本采样
        reg_lambda:      float = 1.0,     # L2 正则
        early_stopping:  int   = 20,      # 早停
    ):
        self.params = {
            "n_estimators":     n_estimators,
            "max_depth":        max_depth,
            "learning_rate":    learning_rate,
            "feature_fraction": feature_fraction,
            "subsample":        subsample,
            "reg_lambda":       reg_lambda,
            "n_jobs":           -1,
            "verbose":          -1,
        }
        self.early_stopping = early_stopping
        self._model = None

    def fit(
        self,
        X_train: pd.DataFrame, y_train: pd.Series,
        X_val:   Optional[pd.DataFrame] = None,
        y_val:   Optional[pd.Series]    = None,
    ) -> "LGBMPredictor":
        try:
            import lightgbm as lgb
        except ImportError:
            raise RuntimeError("请安装：pip install lightgbm")

        self._model = lgb.LGBMRegressor(**self.params)
        fit_kwargs = {}
        if X_val is not None and y_val is not None:
            fit_kwargs = {
                "eval_set":              [(X_val.values, y_val.values)],
                "callbacks":             [lgb.early_stopping(self.early_stopping, verbose=False),
                                          lgb.log_evaluation(-1)],
            }
        self._model.fit(X_train.values, y_train.values, **fit_kwargs)
        return self

    def predict(self, X: pd.DataFrame) -> np.ndarray:
        return self._model.predict(X.values)

    def feature_importance(self, feature_names: list) -> pd.Series:
        imp = self._model.feature_importances_
        return pd.Series(imp, index=feature_names).sort_values(ascending=False)


class EnsemblePredictor:
    """
    集成预测器：Ridge + LightGBM 平均
    借鉴 FinRL 多模型集成思路
    """

    def __init__(self):
        self.ridge = RidgePredictor(alpha=10.0)   # 强正则
        self.lgbm  = LGBMPredictor(max_depth=4)  # 浅树
        self._fitted = False

    def fit(
        self,
        X_train: pd.DataFrame, y_train: pd.Series,
        X_val:   Optional[pd.DataFrame] = None,
        y_val:   Optional[pd.Series]    = None,
    ) -> "EnsemblePredictor":
        self.ridge.fit(X_train, y_train)
        try:
            self.lgbm.fit(X_train, y_train, X_val, y_val)
            self._lgbm_ok = True
        except Exception:
            self._lgbm_ok = False
        self._fitted = True
        return self

    def predict(self, X: pd.DataFrame) -> np.ndarray:
        ridge_pred = self.ridge.predict(X)
        if self._lgbm_ok:
            lgbm_pred = self.lgbm.predict(X)
            return (ridge_pred + lgbm_pred) / 2
        return ridge_pred


# ══════════════════════════════════════════
#  Walk-Forward 训练管道（QLib 核心流程）
# ══════════════════════════════════════════
class MLPipeline:
    """
    机器学习全流程管道
    输入：因子数据 + 未来收益率
    输出：每个时间步的预测信号

    严格遵守 QLib 的 Point-in-Time 原则：
      - 训练只用历史数据
      - 测试不回望训练期
      - 特征在预测时刻必须已知
    """

    def __init__(
        self,
        model_type:    str   = "ensemble",  # ridge / lgbm / ensemble
        forward_days:  int   = 5,           # 预测 N 日后收益
        train_days:    int   = 252,
        test_days:     int   = 63,
        step_days:     int   = 63,
    ):
        self.model_type   = model_type
        self.forward_days = forward_days
        self.train_days   = train_days
        self.test_days    = test_days
        self.step_days    = step_days

    def _make_model(self):
        if self.model_type == "ridge":
            return RidgePredictor(alpha=10.0)
        elif self.model_type == "lgbm":
            return LGBMPredictor()
        else:
            return EnsemblePredictor()

    def run(
        self,
        factor_df:  pd.DataFrame,   # index=date, cols=factors
        price_df:   pd.DataFrame,   # OHLCV
        verbose:    bool = True,
    ) -> pd.Series:
        """
        Walk-Forward 预测

        返回：每个样本外时间步的预测分数（正=看多，负=看空）
        """
        from quant.anti_overfit import WalkForwardConfig, walk_forward_split

        # 计算未来 N 日收益率作为 y
        forward_ret = price_df["close"].pct_change(self.forward_days).shift(-self.forward_days)
        forward_ret.name = "y"

        # 对齐
        combined = factor_df.join(forward_ret, how="inner").dropna()
        if len(combined) < self.train_days + self.test_days:
            print(f"⚠️ 数据不足（{len(combined)} 行），需要 {self.train_days+self.test_days} 行")
            return pd.Series()

        X_all = combined.drop("y", axis=1)
        y_all = combined["y"]

        # Walk-Forward 分割
        cfg = WalkForwardConfig(
            train_days=self.train_days,
            test_days=self.test_days,
            step_days=self.step_days,
        )
        splits = walk_forward_split(combined.index, cfg)

        all_preds = []
        metrics   = []

        for i, (train_idx, test_idx) in enumerate(splits):
            X_train = X_all.loc[train_idx]
            y_train = y_all.loc[train_idx]
            X_test  = X_all.loc[test_idx]
            y_test  = y_all.loc[test_idx]

            # 用最后20%训练数据作为验证集（早停用）
            val_n   = max(10, len(X_train) // 5)
            X_val   = X_train.iloc[-val_n:]
            y_val   = y_train.iloc[-val_n:]
            X_train = X_train.iloc[:-val_n]
            y_train = y_train.iloc[:-val_n]

            model = self._make_model()
            try:
                if isinstance(model, EnsemblePredictor):
                    model.fit(X_train, y_train, X_val, y_val)
                elif isinstance(model, LGBMPredictor):
                    model.fit(X_train, y_train, X_val, y_val)
                else:
                    model.fit(X_train, y_train)

                preds = model.predict(X_test)
                pred_ser = pd.Series(preds, index=test_idx[:len(preds)])
                all_preds.append(pred_ser)

                # 计算 IC
                ic = pred_ser.corr(y_test[:len(preds)], method="spearman")
                metrics.append({"fold": i, "ic": ic, "n_test": len(preds)})
                if verbose:
                    print(f"  Fold {i+1:02d}: IC={ic:+.3f}  样本={len(preds)}")

            except Exception as e:
                print(f"  Fold {i+1:02d}: 失败 — {e}")

        if not all_preds:
            return pd.Series()

        result = pd.concat(all_preds).sort_index()

        if verbose:
            ic_series = pd.Series([m["ic"] for m in metrics])
            from quant.anti_overfit import calc_icir
            print(f"\n  IC均值={ic_series.mean():+.3f}  "
                  f"ICIR={calc_icir(ic_series):+.3f}  "
                  f"IC>0比例={( ic_series>0).mean():.0%}")
            if abs(calc_icir(ic_series)) < 0.3:
                print("  ⚠️ ICIR 低于 0.3，因子预测能力不稳定，建议检查特征工程")

        return result

    def predict_latest(
        self,
        factor_df:  pd.DataFrame,
        price_df:   pd.DataFrame,
        train_days: Optional[int] = None,
    ) -> float:
        """
        用最新 N 天数据训练，预测当前信号（用于实盘）
        返回：预测分数（正=看多）
        """
        n = train_days or self.train_days
        forward_ret = price_df["close"].pct_change(self.forward_days).shift(-self.forward_days)
        combined = factor_df.join(forward_ret.rename("y"), how="inner").dropna()

        if len(combined) < n:
            return 0.0

        train_data = combined.tail(n)
        X_train = train_data.drop("y", axis=1)
        y_train = train_data["y"]

        # 验证集：最后20%
        val_n   = max(5, n // 5)
        X_val   = X_train.iloc[-val_n:]
        y_val   = y_train.iloc[-val_n:]
        X_train = X_train.iloc[:-val_n]
        y_train = y_train.iloc[:-val_n]

        # 预测最新一行因子
        X_latest = factor_df.iloc[[-1]]

        model = self._make_model()
        try:
            model.fit(X_train, y_train, X_val, y_val)
            pred = model.predict(X_latest)
            return float(pred[0])
        except Exception:
            return 0.0
