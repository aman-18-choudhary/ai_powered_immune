import pytest

from sim_engine.benign import gen_benign_txns
from sim_engine.scam import gen_scam_campaign
from sim_engine.world import build_world


@pytest.fixture(scope="session")
def world():
    return build_world(seed=7, n_citizens=1500)


@pytest.fixture(scope="session")
def benign(world):
    return list(gen_benign_txns(world, days=7, seed=11))


@pytest.fixture(scope="session")
def campaigns(world):
    return [
        gen_scam_campaign(world, "camp-A", n_victims=12, seed=21),
        gen_scam_campaign(world, "camp-B", n_victims=6, seed=22),
    ]
