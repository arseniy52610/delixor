import urllib.request
import urllib.error
import json
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
            # Создаем запрос
            data = json.dumps(payload).encode('utf-8')
            req = urllib.request.Request(
                PLATEGA_API_URL,
                data=data,
                headers=headers,
                method='POST'
            )
            
            # Отправляем запрос
            with urllib.request.urlopen(req, timeout=10) as response:
                result = json.loads(response.read().decode('utf-8'))
                
                if result.get("data") and result["data"].get("payment_url"):
                    return {
                        "success": True,
                        "payment_url": result["data"]["payment_url"],
                        "order_id": order_id
                    }
                else:
                    logger.error(f"Platega error: {result}")
                    return None
                    
        except urllib.error.HTTPError as e:
            logger.error(f"Platega HTTP error: {e.code} - {e.reason}")
            try:
                error_body = e.read().decode('utf-8')
                logger.error(f"Error body: {error_body}")
            except:
                pass
            return None
        except urllib.error.URLError as e:
            logger.error(f"Platega URL error: {e.reason}")
            return None
        except Exception as e:
            logger.error(f"Failed to create Platega payment: {e}")
            return None
    
    def verify_callback(self, data: Dict) -> bool:
        """Проверка подлинности callback"""
        required_fields = ["order_id", "status", "amount"]
        return all(field in data for field in required_fields)
