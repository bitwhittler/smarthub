# Mock Data
MOCK_ACCOUNT = {
    "accountNumbers": ["111222333"],
    "customerId": "999000",
    "customerName": "JOHN DOE",
    "type": "RESIDENTIAL",
    "totalBalance": "12.50",
    "lastPayment": "322.53",
    "lastPaymentOn": 1699900000000,
    "dueOn": 1700000000000,
    "primaryServiceLocationId": "555666777",
}

MOCK_LOGIN_SUCCESS = {"status": "SUCCESS", "authorizationToken": "mock-jwt-token"}

MOCK_LOGIN_FAIL = {"status": "FAIL", "message": "Invalid credentials"}

MOCK_ACCTS = [MOCK_ACCOUNT]

MOCK_MAP_ELECTRIC = [
    {
        "account": "111222333",
        "primaryServiceLocationId": "555666777",
        "serviceLocations": ["555666777"],
        "services": ["ELEC"],
        "inactive": False,
    }
]

MOCK_MAP_GAS = [
    {
        "account": "111222333",
        "primaryServiceLocationId": "555666777",
        "serviceLocations": ["555666777"],
        "services": ["GAS"],
        "inactive": False,
    }
]

MOCK_USAGE_PENDING = {"status": "PENDING"}

MOCK_USAGE_COMPLETE = {
    "status": "COMPLETE",
    "data": {
        "ELECTRIC": [
            {
                "type": "USAGE",
                "connectDate": "March 10, 2020",
                "hasDaily": True,
                "hasHourly": True,
                "meters": [{"meterNumber": "METER1", "flowDirection": "FORWARD"}],
                "xToOrderedInterval": {"2026-03-26 14:00": {"interval": {"start": 1774533600000, "end": 1774537200000}}},
                "series": [{"meterNumber": "METER1", "data": [{"x": "2026-03-26 14:00", "y": 1.5}]}],
            },
            {
                "type": "COST",
                "xToOrderedInterval": {"2026-03-26 14:00": {"interval": {"start": 1774533600000, "end": 1774537200000}}},
                "series": [{"meterNumber": "METER1", "data": [{"x": "2026-03-26 14:00", "y": 0.25}]}],
            },
        ]
    },
}
