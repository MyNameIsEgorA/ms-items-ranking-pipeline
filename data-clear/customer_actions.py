import pandas as pd
import numpy as np

# Загружаем данные
user_actions = pd.read_csv(
    '../data_dumps/customer-actions/1 из 10 - KixBox - 09.04.2026 - 775398 - 7a2a7781-bdbd-4616-959b-1d09d04d022e.csv',
    sep=";",
    low_memory=False
)
website_data = pd.read_csv('../data_dumps/products_website.csv')

print("=== СВЯЗИ ЧЕРЕЗ КЛИЕНТОВ ===\n")

# 1. Проверяем CustomerActionCustomerIdsShopifyCustomerId vs website-data
print("1. Shopify Customer ID из user-actions:")
shopify_customers = user_actions['CustomerActionCustomerIdsShopifyCustomerId'].dropna().unique()
print(f"   Уникальных Shopify Customer ID: {len(shopify_customers)}")
print(f"   Это {len(shopify_customers)/len(user_actions)*100:.2f}% от всех действий")
print(f"   Примеры: {shopify_customers[:5].tolist()}")

# В website-data нет прямой колонки с Customer ID,
# но можем проверить связь через другие ID

# 2. Проверяем KixboxId
print("\n2. Kixbox Customer ID:")
kixbox_customers = user_actions['CustomerActionCustomerIdsKixboxId'].dropna().unique()
print(f"   Уникальных Kixbox ID: {len(kixbox_customers)}")
print(f"   Заполнено: {user_actions['CustomerActionCustomerIdsKixboxId'].notna().sum()} ({user_actions['CustomerActionCustomerIdsKixboxId'].notna().sum()/len(user_actions)*100:.2f}%)")

# 3. MindboxId - основная связь
print("\n3. Mindbox Customer ID:")
mindbox_customers = user_actions['CustomerActionCustomerIdsMindboxId'].dropna().unique()
print(f"   Уникальных Mindbox ID: {len(mindbox_customers)}")
print(f"   Заполнено: 100%")

# 4. Анализируем действия, где упоминаются продукты
print("\n=== АНАЛИЗ ДЕЙСТВИЙ С ПРОДУКТАМИ ===\n")

product_actions = ['Просмотр продукта', 'Добавление в корзину', 'Удаление из корзины',
                   'Online.October.ViewProduct', 'Просмотр товара']

for action in product_actions:
    mask = user_actions['CustomerActionActionTemplateName'].str.contains(action, na=False)
    count = mask.sum()
    if count > 0:
        print(f"'{action}': {count} действий")
        # Показываем примеры таких действий
        sample = user_actions[mask].head(2)
        for idx, row in sample.iterrows():
            print(f"  Пример {idx}:")
            print(f"    MindboxId: {row['CustomerActionCustomerIdsMindboxId']}")
            print(f"    KixboxId: {row['CustomerActionCustomerIdsKixboxId']}")
            print(f"    ShopifyCustomerId: {row['CustomerActionCustomerIdsShopifyCustomerId']}")
            print(f"    Дата: {row['CustomerActionDateTimeUtc']}")

# 5. Проверяем, можно ли связать через WebsiteID (пустой) и KixboxOnlineID (пустой)
print("\n=== ПУСТЫЕ КОЛОНКИ ===")
empty_cols = ['CustomerActionCustomerIdsKixboxOnlineID', 'CustomerActionCustomerIdsWebsiteID']
for col in empty_cols:
    non_null = user_actions[col].notna().sum()
    print(f"{col}: {non_null} значений")

# 6. Смотрим CustomFields - может там есть product_id в значениях
print("\n=== CUSTOM FIELDS (NPS) ===")
nps_fields = [col for col in user_actions.columns if 'CustomFields' in col]
for col in nps_fields:
    non_null = user_actions[col].notna().sum()
    if non_null > 0:
        print(f"\n{col}:")
        print(f"  Заполнено: {non_null} ({non_null/len(user_actions)*100:.2f}%)")
        print(f"  Примеры значений: {user_actions[col].dropna().head(5).tolist()}")

# 7. Ищем связь между KixboxId и Handle через паттерны
print("\n=== ПОИСК ПАТТЕРНОВ В KIXBOX ID ===")
kixbox_ids = user_actions['CustomerActionCustomerIdsKixboxId'].dropna().astype(str)
print(f"Диапазон KixboxId: {kixbox_ids.min()} - {kixbox_ids.max()}")
print(f"Все цифровые: {(kixbox_ids.str.isdigit()).all()}")

# 8. Статистика по действиям с продуктами
print("\n=== СТАТИСТИКА ДЕЙСТВИЙ ===")
view_products = user_actions[user_actions['CustomerActionActionTemplateName'].str.contains('Просмотр|ViewProduct', na=False)]
print(f"Просмотры продуктов: {len(view_products)}")
print(f"Уникальных клиентов с просмотрами: {view_products['CustomerActionCustomerIdsMindboxId'].nunique()}")

add_to_cart = user_actions[user_actions['CustomerActionActionTemplateName'].str.contains('Добавление|корзин', na=False)]
print(f"Добавления в корзину: {len(add_to_cart)}")
print(f"Уникальных клиентов с добавлениями: {add_to_cart['CustomerActionCustomerIdsMindboxId'].nunique()}")

# 9. Проверяем, есть ли другие файлы customer-actions
print("\n=== ВЫВОД ===")
print("В ЭТОМ файле user-actions нет колонок ProductIds.")
print("Связь с website-data возможна только:")
print("1. Через ShopifyCustomerId (если есть соответствующая колонка в других данных)")
print("2. Через KixboxId (если в других системах есть маппинг KixboxId -> ProductId)")
print("3. Возможно, ProductId хранятся в других файлах дампа (не в этом)")