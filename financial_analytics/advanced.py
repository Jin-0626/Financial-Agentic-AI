"""Bounded adapters for installed offline numerical libraries."""
import numpy as np


def calculate_advanced(operation, a):
    if operation == "implied_volatility":
        from scipy.optimize import brentq
        from financial_analytics.calculations import black_scholes
        def objective(v): return float(black_scholes(a.model_copy(update={"volatility":v}))["price"])-float(a.market_price)
        volatility=brentq(objective,0.00001,5,xtol=1e-10)
        return {"implied_volatility":volatility,"method":"European Black-Scholes inversion"}
    if operation == "stationarity_tests":
        from statsmodels.tsa.stattools import adfuller,kpss
        if len(a.values)<20 or np.ptp(a.values)==0: raise ValueError("Stationarity tests require 20 nonconstant observations")
        adf=adfuller(a.values,maxlag=min(5,len(a.values)//4-1),autolag="AIC");kp=kpss(a.values,regression="c",nlags="auto")
        return {"adf_statistic":float(adf[0]),"adf_p_value":float(adf[1]),"kpss_statistic":float(kp[0]),"kpss_p_value":float(kp[1]),"assumptions":["ADF null: unit root","KPSS null: level stationarity"]}
    if operation == "autocorrelation":
        from statsmodels.tsa.stattools import acf
        if np.ptp(a.values)==0: raise ValueError("Autocorrelation requires varying observations")
        return {"values":acf(a.values,nlags=min(20,len(a.values)-1),fft=True).tolist()}
    if operation in {"covariance_matrix","fortitudo_covariance"}:
        x=np.asarray(a.values)
        if np.any(x.std(axis=0)==0): raise ValueError("Covariance inputs require varying columns")
        if operation == "fortitudo_covariance":
            from fortitudo.tech import covariance_matrix
            covariance=np.asarray(covariance_matrix(x))
        else: covariance=np.cov(x,rowvar=False)
        return {"covariance":np.atleast_2d(covariance).tolist(),"correlation":np.atleast_2d(np.corrcoef(x,rowvar=False)).tolist()}
    if operation == "garch_volatility":
        from arch import arch_model
        if len(a.returns)<30 or np.std(a.returns)==0: raise ValueError("GARCH requires 30 varying returns")
        fit=arch_model(np.asarray(a.returns)*100,p=1,q=1,mean="Constant",rescale=False).fit(disp="off",options={"maxiter":500})
        if fit.convergence_flag!=0: raise ValueError("GARCH fit did not converge")
        variance=float(fit.forecast(horizon=1,reindex=False).variance.iloc[-1,0])/10000
        return {"next_period_variance":variance,"next_period_volatility":float(np.sqrt(variance)),"units":"return fraction"}
    if operation == "auto_arima_forecast":
        from pmdarima import auto_arima
        fit=auto_arima(a.values,start_p=0,start_q=0,max_p=3,max_q=3,max_d=1,seasonal=False,stepwise=True,maxiter=50,error_action="raise",suppress_warnings=False)
        predicted,interval=fit.predict(n_periods=a.horizon,return_conf_int=True)
        return {"forecast":predicted.tolist(),"interval_95":interval.tolist(),"selected_order":list(fit.order)}
    if operation == "seasonal_naive_forecast":
        import pandas as pd
        from gluonts.dataset.common import ListDataset
        from gluonts.model.seasonal_naive import SeasonalNaivePredictor
        predictor=SeasonalNaivePredictor(prediction_length=a.horizon,season_length=min(7,len(a.values)))
        data=ListDataset([{"start":pd.Period("2000-01-01",freq="D"),"target":a.values}],freq="D")
        forecast=next(predictor.predict(data))
        return {"forecast":np.asarray(forecast.mean).tolist(),"assumption":"Consecutive daily observations, repeating last seven observations"}
    if operation == "smooth_timeseries":
        from tsmoothie.smoother import ExponentialSmoother
        smoother=ExponentialSmoother(window_len=min(10,len(a.values)),alpha=0.3).smooth(a.values)
        return {"values":np.asarray(smoother.smooth_data).reshape(-1).tolist(),"method":"Exponential smoothing, alpha 0.3"}
    if operation == "gs_timeseries_statistics":
        import pandas as pd
        from gs_quant.timeseries import mean,std
        x=pd.Series(a.prices)
        def clean(series): return [None if not np.isfinite(value) else float(value) for value in series]
        return {"mean":clean(mean(x,a.window)),"standard_deviation":clean(std(x,a.window)),"method":"Offline trailing GS-Quant statistics"}
    if operation == "neural_regression":
        from sklearn.neural_network import MLPRegressor
        from sklearn.pipeline import make_pipeline
        from sklearn.preprocessing import StandardScaler
        from sklearn.metrics import mean_absolute_error
        x=np.asarray(a.values);y=np.asarray(a.target);split=int(len(x)*(1-a.test_fraction))
        if split<5 or len(x)-split<2: raise ValueError("Insufficient chronological training/holdout rows")
        fit=make_pipeline(StandardScaler(),MLPRegressor(hidden_layer_sizes=(32,16),max_iter=300,random_state=a.seed,early_stopping=False)).fit(x[:split],y[:split])
        model=fit[-1]
        if model.n_iter_>=model.max_iter: raise ValueError("Neural regression did not converge")
        predicted=fit.predict(x[split:])
        return {"predictions":predicted.tolist(),"holdout_mae":float(mean_absolute_error(y[split:],predicted)),"train_rows":split,"holdout_rows":len(x)-split,"assumption":"Train-only scaling and chronological holdout"}
    if operation in {"descriptive_statistics", "normality_test", "mean_comparison", "correlation"}:
        from scipy import stats
        x = np.asarray(a.values)
        if operation == "descriptive_statistics":
            mean = float(x.mean()); sem = float(stats.sem(x)); bound = float(stats.t.ppf((1+a.confidence)/2,len(x)-1))*sem
            return {"mean": mean, "sample_variance": float(x.var(ddof=1)), "sample_standard_deviation": float(x.std(ddof=1)),
                    "minimum": float(x.min()), "maximum": float(x.max()), "median": float(np.median(x)), "mean_interval": [mean-bound,mean+bound], "confidence": a.confidence}
        if operation == "normality_test":
            if len(x) < 3 or len(x) > 5000 or np.ptp(x) == 0: raise ValueError("Shapiro requires 3..5000 nonconstant observations")
            result = stats.shapiro(x)
            return {"statistic": float(result.statistic), "p_value": float(result.pvalue)}
        if a.comparison is None: raise ValueError("Comparison observations are required")
        y = np.asarray(a.comparison)
        if x.std() == 0 or y.std() == 0: raise ValueError("Samples must have nonzero variance")
        if operation == "mean_comparison":
            result = stats.ttest_ind(x,y,equal_var=False)
            return {"statistic": float(result.statistic), "p_value": float(result.pvalue), "method": "Welch independent samples"}
        if len(x) != len(y): raise ValueError("Paired samples must align")
        p = stats.pearsonr(x,y); r = stats.spearmanr(x,y)
        return {"pearson": float(p.statistic), "pearson_p_value": float(p.pvalue), "spearman": float(r.statistic), "spearman_p_value": float(r.pvalue)}
    if operation == "linear_forecast":
        x = np.column_stack([np.ones(len(a.values)), np.arange(len(a.values))])
        coefficients = np.linalg.lstsq(x, np.asarray(a.values), rcond=None)[0]
        return {"forecast": (coefficients[0]+coefficients[1]*np.arange(len(a.values),len(a.values)+a.horizon)).tolist(), "assumption": "Linear historical trend, no prediction interval"}
    if operation == "arima_forecast":
        from statsmodels.tsa.arima.model import ARIMA
        if len(a.values) < max(10, sum(a.order)*3): raise ValueError("Insufficient observations for ARIMA order")
        fit = ARIMA(a.values, order=a.order).fit()
        if not fit.mle_retvals.get("converged", True): raise ValueError("ARIMA optimization did not converge")
        forecast = fit.get_forecast(a.horizon)
        return {"forecast": np.asarray(forecast.predicted_mean).tolist(), "interval_95": np.asarray(forecast.conf_int()).tolist(), "aic": float(fit.aic)}
    if operation == "exponential_smoothing":
        from statsmodels.tsa.holtwinters import ExponentialSmoothing
        fit = ExponentialSmoothing(a.values,trend="add",initialization_method="estimated").fit()
        return {"forecast": np.asarray(fit.forecast(a.horizon)).tolist(), "method": "Holt additive trend"}
    if operation == "linear_regression":
        import statsmodels.api as sm
        x = sm.add_constant(np.asarray(a.values),has_constant="add")
        if np.linalg.matrix_rank(x) < x.shape[1]: raise ValueError("Regression design is rank deficient")
        fit = sm.OLS(a.target,x,missing="raise").fit()
        return {"coefficients": fit.params.tolist(), "p_values": fit.pvalues.tolist(), "confidence_intervals_95": fit.conf_int().tolist(), "r_squared": float(fit.rsquared)}
    if operation == "principal_components":
        from sklearn.decomposition import PCA
        from sklearn.preprocessing import StandardScaler
        x = StandardScaler().fit_transform(a.values)
        if np.sum(x*x) == 0: raise ValueError("PCA requires varying observations")
        fit = PCA(n_components=min(10,len(a.values[0]))).fit(x)
        return {"components": fit.components_.tolist(), "explained_variance_ratio": fit.explained_variance_ratio_.tolist()}
    if operation == "random_forest_regression":
        from sklearn.ensemble import RandomForestRegressor
        from sklearn.metrics import mean_absolute_error, mean_squared_error
        x = np.asarray(a.values); y = np.asarray(a.target)
        split = int(len(x)*(1-a.test_fraction))
        if split < 5 or len(x)-split < 2: raise ValueError("Insufficient chronological training/holdout rows")
        fit = RandomForestRegressor(n_estimators=100,max_depth=8,random_state=a.seed,n_jobs=1).fit(x[:split],y[:split])
        predicted = fit.predict(x[split:])
        return {"predictions": predicted.tolist(), "feature_importance": fit.feature_importances_.tolist(),
                "holdout_mae": float(mean_absolute_error(y[split:],predicted)), "holdout_rmse": float(np.sqrt(mean_squared_error(y[split:],predicted))),
                "train_rows": split, "holdout_rows": len(x)-split, "assumption": "Chronological holdout; no shuffled future observations"}
    if operation in {"portfolio_optimization", "pyportfolioopt_allocation", "skfolio_allocation", "riskfolio_allocation"}:
        from scipy.optimize import minimize
        import pandas as pd
        x = np.asarray(a.values); covariance = np.cov(x,rowvar=False)*a.periods_per_year
        mean = x.mean(axis=0)*a.periods_per_year; n = x.shape[1]
        if np.min(np.linalg.eigvalsh(covariance)) < -1e-10 or np.max(np.abs(covariance)) < 1e-14: raise ValueError("Covariance must be nonzero and positive semidefinite")
        if operation == "pyportfolioopt_allocation":
            from pypfopt import EfficientFrontier
            fit = EfficientFrontier(pd.Series(mean),pd.DataFrame(covariance),weight_bounds=(0,a.max_weight))
            if a.method == "minimum_variance": weights = fit.min_volatility()
            elif a.method == "maximum_sharpe": weights = fit.max_sharpe(risk_free_rate=a.annual_risk_free_rate)
            else: raise ValueError("Risk parity is supported by portfolio_optimization")
            w = np.asarray(list(weights.values()))
        elif operation == "skfolio_allocation":
            from skfolio.optimization import MeanRisk, ObjectiveFunction
            from skfolio import RiskMeasure
            if a.method == "risk_parity": raise ValueError("Use portfolio_optimization for risk parity")
            objective = ObjectiveFunction.MINIMIZE_RISK if a.method == "minimum_variance" else ObjectiveFunction.MAXIMIZE_RATIO
            fit = MeanRisk(risk_measure=RiskMeasure.VARIANCE,objective_function=objective,min_weights=0,max_weights=a.max_weight,
                           risk_free_rate=a.annual_risk_free_rate/a.periods_per_year).fit(x)
            w = np.asarray(fit.weights_)
        elif operation == "riskfolio_allocation":
            import riskfolio as rp
            fit = rp.Portfolio(returns=pd.DataFrame(x));fit.assets_stats(method_mu="hist",method_cov="hist")
            fit.upperlng=a.max_weight
            if a.method == "risk_parity": result=fit.rp_optimization(model="Classic",rm="MV",rf=a.annual_risk_free_rate/a.periods_per_year)
            else: result=fit.optimization(model="Classic",rm="MV",obj="MinRisk" if a.method == "minimum_variance" else "Sharpe",rf=a.annual_risk_free_rate/a.periods_per_year,hist=True)
            if result is None: raise ValueError("Riskfolio did not find a feasible solution")
            w = np.asarray(result).reshape(-1)
        else:
            def objective(w):
                variance = float(w@covariance@w)
                if a.method == "minimum_variance": return variance
                if a.method == "maximum_sharpe": return -(float(w@mean)-a.annual_risk_free_rate)/np.sqrt(max(variance,1e-20))
                contributions = w*(covariance@w)
                return float(np.sum((contributions-contributions.mean())**2)) / max(variance**2,1e-20)
            fit=minimize(objective,np.full(n,1/n),method="SLSQP",bounds=[(0,a.max_weight)]*n,
                         constraints=[{"type":"eq","fun":lambda w:w.sum()-1}],options={"maxiter":1000,"ftol":1e-12})
            if not fit.success: raise ValueError("Allocation solver did not converge")
            w=fit.x
        if not np.all(np.isfinite(w)) or abs(w.sum()-1)>1e-6 or np.min(w)<-1e-7 or np.max(w)>a.max_weight+1e-6: raise ValueError("Solver returned invalid allocation")
        return {"weights": w.tolist(), "annualized_expected_return": float(w@mean), "annualized_volatility": float(np.sqrt(w@covariance@w)),
                "assumption": "Historical arithmetic mean and sample covariance; long-only weights"}
    raise ValueError("Operation has no reviewed implementation")
