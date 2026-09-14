"""Run the broker on the loopback interface only."""

import uvicorn

from governor.config import GovernorPaths
from governor.store import ControlStore
from venue_broker.app import create_app
from venue_broker.broker import Broker
from venue_broker.config import BROKER_HOST, Settings
from venue_broker.control_policy import ControlPolicyReader


def main() -> None:
    settings = Settings.from_env()
    paths = GovernorPaths.default()
    ControlStore.create(paths.database, policy_target=paths.broker_policy)
    policy_reader = ControlPolicyReader(settings.control_policy_path)
    policy_reader.validate()
    app = create_app(Broker(settings, policy_reader=policy_reader))
    uvicorn.run(
        app,
        host=BROKER_HOST,
        port=settings.port,
        access_log=False,
        log_level="info",
    )


if __name__ == "__main__":
    main()
