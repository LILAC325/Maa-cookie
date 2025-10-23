import sys

from maa.agent.agent_server import AgentServer
from maa.toolkit import Toolkit

import base

def main():
    Toolkit.init_option("032f874d-d96c-4f2a-86a7-df10dbb80de6")

    socket_id = sys.argv[-1]

    AgentServer.start_up(socket_id)
    AgentServer.join()
    AgentServer.shut_down()


if __name__ == "__main__":
    main()