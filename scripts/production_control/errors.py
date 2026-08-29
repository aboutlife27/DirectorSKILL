class ProductionError(Exception):
    def __init__(self, message, code="production_error"):
        super().__init__(message)
        self.code = code
