from abc import ABC, abstractmethod


class Detector(ABC):
    @abstractmethod
    def load(self): ...
    @abstractmethod
    def set_classes(self, classes): ...
    @abstractmethod
    def predict(self, image, confidence=0.25): ...
