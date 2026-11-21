"""石头剪刀布裁判 + 计分。纯逻辑，无副作用，便于测试。"""

MOVES = ("rock", "paper", "scissors")
BEATS = {"rock": "scissors", "scissors": "paper", "paper": "rock"}
ZH = {"rock": "石头", "paper": "布", "scissors": "剪刀"}
RESULT_ZH = {"human": "你赢了", "agent": "机械手赢了", "draw": "平局"}


def judge(human, agent):
    """返回 'human' / 'agent' / 'draw'。"""
    if human == agent:
        return "draw"
    if BEATS[human] == agent:
        return "human"
    return "agent"


class Score:
    def __init__(self):
        self.human = 0
        self.agent = 0
        self.draw = 0
        self.rounds = 0

    def add(self, result):
        self.rounds += 1
        if result == "human":
            self.human += 1
        elif result == "agent":
            self.agent += 1
        else:
            self.draw += 1

    def __str__(self):
        return f"人 {self.human} : {self.agent} 机械手（平 {self.draw}，共 {self.rounds} 局）"
