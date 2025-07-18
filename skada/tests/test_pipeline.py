# Author: Theo Gnassounou <theo.gnassounou@inria.fr>
#         Remi Flamary <remi.flamary@polytechnique.edu>
#         Oleksii Kachaiev <kachayev@gmail.com>
#
# License: BSD 3-Clause

import numpy as np
import pytest
from numpy.testing import assert_array_equal
from sklearn.base import BaseEstimator
from sklearn.decomposition import PCA
from sklearn.linear_model import LogisticRegression
from sklearn.preprocessing import StandardScaler
from sklearn.svm import SVC
from sklearn.utils.metaestimators import available_if

from skada import (
    CORAL,
    CORALAdapter,
    PerDomain,
    Shared,
    SubspaceAlignmentAdapter,
    make_da_pipeline,
    source_target_split,
)
from skada.base import BaseAdapter
from skada.datasets import DomainAwareDataset


def test_pipeline(da_dataset):
    # single source, single target, target labels are masked
    X, y, sample_domain = da_dataset.pack(
        as_sources=["s"], as_targets=["t"], mask_target_labels=True
    )
    # by default, each estimator in the pipeline is wrapped into `Shared` selector
    pipe = make_da_pipeline(
        StandardScaler(),
        None,
        PCA(),
        SubspaceAlignmentAdapter(n_components=2),
        LogisticRegression(),
    )
    # 'sample_domain' is requested by the selector (`Shared` in this case)
    pipe.fit(X, y, sample_domain=sample_domain)
    # fails when passing source into predict
    with pytest.raises(ValueError):
        pipe.predict(X, sample_domain=sample_domain)
    with pytest.raises(ValueError):
        pipe.score(X, y, sample_domain=sample_domain)
    # target only, no label masking
    X_target, y_target, sample_domain = da_dataset.pack(
        as_sources=[], as_targets=["t"], mask_target_labels=False
    )
    y_pred = pipe.predict(X_target, sample_domain=sample_domain)
    assert np.mean(y_pred == y_target) > 0.9
    # automatically derives as a single target domain when sample_domain is `None`
    y_pred_derived = pipe.predict(X_target)
    assert_array_equal(y_pred, y_pred_derived, "automatically derives as target")
    # proxy to the default scoring of the final estimator
    # (`LogisticRegression` in this case)
    score = pipe.score(X_target, y_target, sample_domain=sample_domain)
    assert score > 0.9
    score_derived = pipe.score(X_target, y_target)
    assert score == score_derived, "automatically derives as target"


def test_per_domain_selector():
    scaler = make_da_pipeline(PerDomain(StandardScaler()))
    X = np.array([[1.0, 0.0], [0.0, 8.0], [3.0, 0.0], [0.0, 0.0]])
    sample_domain = np.array([1, 2, 1, 2])
    scaler.fit(X, y=None, sample_domain=sample_domain)
    scaler.fit_transform(X, y=None, sample_domain=sample_domain)
    assert_array_equal(
        np.array([[-3.0, 1.0]]),
        scaler.transform(np.array([[-1.0, 1.0]]), sample_domain=np.array([1])),
    )
    assert_array_equal(
        np.array([[-1.0, -0.75]]),
        scaler.transform(np.array([[-1.0, 1.0]]), sample_domain=np.array([2])),
    )


@pytest.mark.parametrize(
    "selector_name, selector_cls",
    [
        ("per_domain", PerDomain),
        ("shared", Shared),
        (PerDomain, PerDomain),
        (lambda x: PerDomain(x), PerDomain),
        pytest.param(
            "non_existing_one",
            None,
            marks=pytest.mark.xfail(reason="Fails non-existing selector"),
        ),
        pytest.param(
            42, None, marks=pytest.mark.xfail(reason="Fails uninterpretable type")
        ),
        pytest.param(
            lambda x: 42,
            None,
            marks=pytest.mark.xfail(reason="Incorrect output type for the callable"),
        ),
    ],
)
def test_default_selector_parameter(selector_name, selector_cls):
    pipe = make_da_pipeline(
        SubspaceAlignmentAdapter(n_components=2),
        LogisticRegression(),
        default_selector=selector_name,
    )
    _, estimator = pipe.steps[0]
    assert isinstance(estimator, selector_cls)


def test_default_selector_ignored_for_selector():
    pipe = make_da_pipeline(
        Shared(SubspaceAlignmentAdapter(n_components=2)),
        LogisticRegression(),
        default_selector="per_domain",
    )
    name, estimator = pipe.steps[0]
    assert isinstance(estimator, Shared)
    assert name == "subspacealignmentadapter"

    name, estimator = pipe.steps[1]
    assert isinstance(estimator, PerDomain)
    assert name == "perdomain_logisticregression"


def test_pipeline_step_parameters():
    pipe = make_da_pipeline(
        StandardScaler(),
        PCA(),
        SubspaceAlignmentAdapter(n_components=2),
        LogisticRegression(),
    )
    pipe.set_params(subspacealignmentadapter__n_components=5)
    with pytest.raises(ValueError):
        pipe.set_params(subspacealignmentadapter__reg=2.0)


def test_named_estimator():
    pipe = make_da_pipeline(
        PerDomain(StandardScaler()),
        ("adapter", SubspaceAlignmentAdapter(n_components=2)),
        PCA(n_components=4),
        PCA(n_components=2),
        CORAL(),
        ("othercoral", CORAL()),
        LogisticRegression(),
    )
    assert "adapter" in pipe.named_steps
    assert "perdomain_standardscaler" in pipe.named_steps
    assert "pca-1" in pipe.named_steps
    assert "pca-2" in pipe.named_steps
    assert "logisticregression" in pipe.named_steps
    assert "coraladapter" in pipe.named_steps
    assert "othercoral__coraladapter" in pipe.named_steps


def test_empty_pipeline():
    with pytest.raises(TypeError):
        make_da_pipeline()


def test_unwrap_nested_da_pipelines(da_dataset):
    X, y, sample_domain = da_dataset.pack(
        as_sources=["s"],
        as_targets=["t"],
        mask_target_labels=True,
    )

    # make a DA pipeline from scratch
    clf = make_da_pipeline(StandardScaler(), CORALAdapter(), SVC(kernel="rbf"))
    clf.fit(X, y, sample_domain=sample_domain)
    y_pred = clf.predict(X[sample_domain < 0])

    # use pre-defined DA pipeline as a step
    nested_clf = make_da_pipeline(StandardScaler(), CORAL())
    nested_clf.fit(X, y, sample_domain=sample_domain)
    y_nested_pred = nested_clf.predict(X[sample_domain < 0])

    # compare outputs
    assert np.allclose(y_pred, y_nested_pred)


@pytest.mark.parametrize("_fit_transform", [(True,), (False,)])
def test_allow_nd_x(_fit_transform):
    class CutInputDim(BaseEstimator):
        def fit(self, X, y=None, **params):
            self.fitted_ = True

        def transform(self, X):
            return X[:, :, 0]

        @available_if(lambda _: _fit_transform)
        def fit_transform(self, X, y=None, **params):
            self.fit(X, y=y, **params)
            return self.transform(X)

    pipe = make_da_pipeline(CutInputDim(), CORALAdapter())

    rng = np.random.default_rng(42)
    Xs = rng.standard_normal(size=(100, 22, 30))
    Xt = rng.standard_normal(size=(100, 22, 30))
    ys = rng.integers(0, 2, 100)
    yt = rng.integers(0, 2, 100)

    dataset = DomainAwareDataset()
    dataset.add_domain(Xs, ys, "source")
    dataset.add_domain(Xt, yt, "target")

    X, y, sample_domain = dataset.pack(
        as_sources=["source"], as_targets=["target"], mask_target_labels=True
    )
    pipe.fit(X, y, sample_domain=sample_domain)


def test_adaptation_output_propagate_labels(da_reg_dataset):
    X, y, sample_domain = da_reg_dataset.pack(
        as_sources=["s"], as_targets=["t"], mask_target_labels=False
    )
    _, X_target, _, target_domain = source_target_split(
        X, sample_domain, sample_domain=sample_domain
    )
    output = {}

    class FakeAdapter(BaseAdapter):
        def fit_transform(self, X, y=None, sample_domain=None):
            self.fitted_ = True
            if y is not None:
                assert not np.any(np.isnan(y)), "Expect unmasked labels"
                y[::2] = np.nan
            return X, y, dict()

    class FakeEstimator(BaseEstimator):
        def fit(self, X, y=None, **params):
            output["fit_n_samples"] = X.shape[0]
            self.fitted_ = True
            return self

        def predict(self, X):
            return X

    clf = make_da_pipeline(
        StandardScaler(),
        FakeAdapter(),
        FakeEstimator(),
    )

    # check no errors are raised
    clf.fit(X, y, sample_domain=sample_domain)
    clf.predict(X_target, sample_domain=target_domain)

    # output should contain only half of targets
    assert output["fit_n_samples"] == X.shape[0] // 2
