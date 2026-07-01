import requests
import logging
from typing import Optional, Dict

logger = logging.getLogger(__name__)

PLATEGA_API_URL = "https://app.platega.io/api/merchant"

class PlategaPayment:
    def __init__(self, merchant_id: str, secret_key: str):
        self.merchant_id = merchant_id
        self.secret_key = secret_key
        
    def create_payment(self, user_id: int, amount: float, plan: str, order_id: str, callback_url: str) -> Optional[Dict]:
        """Создание платежа в Platega.io"""
        
        headers = {
            "X-MerchantId": self.merchant_id,
            "X-Secret": self.secret_key,
            "Content-Type": "application/json"
        }
        
        payload = {
            "amount": amount,
            "currency": "RUB",
            "order_id": order_id,
            "description": f"Подписка Delixor Plus - {plan}",
            "method": 2,  # СБП
            "url_status": callback_url,
            "lifetime": 3600  # Время жизни платежа 1 час
        }
        
        try:
            response = requests.post(PLATEGA_API_URL, json=payload, headers=headers, timeout=10)
            response.raise_for_status()
            result = response.json()
            
            if result.get("data") and result["data"].get("payment_url"):
                return {
                    "success": True,
                    "payment_url": result["data"]["payment_url"],
                    "order_id": order_id
                }
            else:
                logger.error(f"Platega error: {result}")
                return None
                
        except Exception as e:
            logger.error(f"Failed to create Platega payment: {e}")
            return None
    
    def verify_callback(self, data: Dict) -> bool:
        """Проверка подлинности callback"""
        required_fields = ["order_id", "status", "amount"]
        return all(field in data for field in required_fields)
