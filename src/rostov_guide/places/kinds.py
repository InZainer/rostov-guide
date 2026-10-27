"""Словарь видов мест и кухонь: OSM-теги по-русски.

Нужен в трёх местах: в карточках для пользователя, в тексте документов для
векторного индекса и в разборе аргумента `kind`, который модель может прислать
как `pharmacy`, `аптека` или `аптеки` — всё это один и тот же вид.
"""

from __future__ import annotations

import re

KIND_LABELS: dict[str, str] = {
    # еда и напитки
    "restaurant": "ресторан", "cafe": "кафе", "fast_food": "фастфуд", "bar": "бар",
    "pub": "паб", "ice_cream": "мороженое", "nightclub": "ночной клуб",
    "confectionery": "кондитерская", "bakery": "пекарня", "pastry": "кондитерская",
    "coffee": "кофе (магазин)", "tea": "чайный магазин", "deli": "деликатесы",
    "seafood": "рыба и морепродукты", "butcher": "мясная лавка", "alcohol": "алкомаркет",
    "wine": "винный магазин", "beverages": "напитки", "greengrocer": "овощи и фрукты",
    # магазины
    "supermarket": "супермаркет", "convenience": "продукты у дома", "mall": "торговый центр",
    "marketplace": "рынок", "department_store": "универмаг", "kiosk": "киоск",
    "clothes": "одежда", "shoes": "обувь", "fashion": "мода", "jewelry": "ювелирный",
    "cosmetics": "косметика", "perfumery": "парфюмерия", "chemist": "бытовая химия",
    "electronics": "электроника", "mobile_phone": "салон связи", "computer": "компьютеры",
    "furniture": "мебель", "hardware": "хозтовары", "doityourself": "стройматериалы",
    "houseware": "товары для дома", "books": "книги", "stationery": "канцтовары",
    "toys": "игрушки", "baby_goods": "детские товары", "pet": "зоомагазин",
    "florist": "цветы", "gift": "подарки", "sports": "спорттовары", "outdoor": "туризм и отдых",
    "bicycle": "велосипеды", "optician": "оптика", "tobacco": "табак",
    "e-cigarette": "электронные сигареты", "variety_store": "всё по одной цене",
    "second_hand": "секонд-хенд", "car": "автосалон", "car_parts": "автозапчасти",
    "tyres": "шины", "power_tools": "электроинструмент", "tiles": "плитка", "doors": "двери",
    "fabric": "ткани", "bed": "товары для сна", "medical_supply": "медтовары",
    "newsagent": "пресса", "farm": "фермерские продукты", "water_filter": "фильтры для воды",
    "fishing": "рыбалка", "hunting": "охота", "musical_instrument": "музыкальные инструменты",
    "video_games": "видеоигры", "photo": "фото", "copyshop": "копицентр",
    # услуги
    "outpost": "пункт выдачи заказов", "post_office": "почта", "bank": "банк", "atm": "банкомат",
    "car_repair": "автосервис", "car_wash": "автомойка", "fuel": "АЗС", "car_rental": "прокат авто",
    "beauty": "салон красоты", "hairdresser": "парикмахерская", "massage": "массаж",
    "tattoo": "тату-салон", "dry_cleaning": "химчистка", "laundry": "прачечная",
    "tailor": "ателье", "shoe_repair": "ремонт обуви", "locksmith": "изготовление ключей",
    "pawnbroker": "ломбард", "money_lender": "микрозаймы", "bookmaker": "букмекер",
    "travel_agency": "турагентство", "travel_agent": "турагентство", "estate_agent": "агентство недвижимости",
    "lawyer": "юрист", "company": "компания", "it": "IT-компания", "coworking": "коворкинг",
    "funeral_directors": "ритуальные услуги", "ticket": "билетная касса",
    # здоровье
    "pharmacy": "аптека", "hospital": "больница", "clinic": "поликлиника", "doctors": "врач",
    "dentist": "стоматология", "veterinary": "ветклиника", "laboratory": "лаборатория",
    "birthing_centre": "роддом",
    # образование, досуг, жильё
    "school": "школа", "kindergarten": "детский сад", "university": "университет",
    "college": "колледж", "library": "библиотека", "cinema": "кинотеатр", "theatre": "театр",
    "museum": "музей", "gallery": "галерея", "attraction": "достопримечательность",
    "park": "парк", "playground": "детская площадка", "stadium": "стадион",
    "sports_centre": "спортивный центр", "fitness_centre": "фитнес-клуб", "swimming_pool": "бассейн",
    "hotel": "гостиница", "hostel": "хостел", "guest_house": "гостевой дом", "apartment": "апартаменты",
}

# Зачем туда идут: словами пользователя, а не тегом. Как и блюда кухонь, это
# расширение документа для поиска «по смыслу», пользователю не показывается.
KIND_NEEDS: dict[str, str] = {
    "pharmacy": "лекарства, таблетки", "atm": "снять наличные, деньги", "bank": "снять наличные, кредит, вклад",
    "fuel": "заправить машину, бензин, заправка", "car_wash": "помыть машину",
    "car_repair": "починить машину, шиномонтаж", "playground": "погулять с ребёнком, качели, горка",
    "park": "погулять, прогулка, зелень", "post_office": "отправить письмо или посылку",
    "outpost": "забрать заказ, отправить посылку", "hairdresser": "постричься, стрижка",
    "beauty": "маникюр, стрижка, косметолог", "florist": "букет, купить цветы",
    "supermarket": "купить продукты", "convenience": "купить продукты рядом с домом",
    "bakery": "хлеб, выпечка", "confectionery": "торты, пирожные, сладости",
    "pastry": "торты, пирожные", "ice_cream": "мороженое, десерт", "pub": "выпить пива",
    "bar": "выпить, коктейли", "alcohol": "купить алкоголь, пиво, вино", "nightclub": "потанцевать, вечеринка",
    "cafe": "поесть, кофе, перекусить", "restaurant": "поужинать, пообедать",
    "fast_food": "быстро перекусить, еда с собой", "cinema": "посмотреть фильм, кино",
    "theatre": "спектакль", "museum": "экскурсия, выставка", "hotel": "переночевать, снять номер",
    "hostel": "переночевать недорого", "dentist": "вылечить зубы", "clinic": "к врачу, анализы",
    "hospital": "лечение, скорая помощь", "veterinary": "лечить кошку или собаку", "pet": "корм для животных",
    "fitness_centre": "тренировки, спортзал", "swimming_pool": "поплавать", "laundry": "постирать вещи",
    "dry_cleaning": "почистить одежду", "shoe_repair": "починить обувь", "tailor": "подшить одежду",
    "mobile_phone": "купить телефон, сим-карта", "copyshop": "распечатать, ксерокопия",
    "library": "почитать, взять книгу", "books": "купить книгу", "optician": "очки, линзы",
}

# Группы видов: для фильтра «только где поесть» и для подсказок модели.
GROUPS: dict[str, tuple[str, ...]] = {
    "food": ("restaurant", "cafe", "fast_food", "bar", "pub", "ice_cream", "confectionery",
             "bakery", "pastry", "nightclub"),
    "shop": ("supermarket", "convenience", "mall", "marketplace", "clothes", "shoes", "electronics",
             "cosmetics", "furniture", "hardware", "books", "florist", "pet", "alcohol", "butcher"),
    "health": ("pharmacy", "hospital", "clinic", "doctors", "dentist", "veterinary", "laboratory"),
    "services": ("outpost", "post_office", "bank", "atm", "car_repair", "car_wash", "fuel",
                 "beauty", "hairdresser", "dry_cleaning"),
    "leisure": ("park", "cinema", "theatre", "museum", "attraction", "sports_centre",
                "fitness_centre", "swimming_pool", "stadium", "playground"),
    "education": ("school", "kindergarten", "university", "college", "library"),
    "lodging": ("hotel", "hostel", "guest_house", "apartment"),
}

# Кухни OSM по-русски, с типичными блюдами. Блюда идут только в текст документа
# для векторного поиска — это расширение документа словарём, а не факт о меню,
# поэтому пользователю показывается лишь название кухни.
CUISINES: dict[str, tuple[str, str]] = {
    "russian": ("русская", "пельмени, борщ, блины, вареники, щи, котлеты"),
    "regional": ("местная", "донская кухня, раки, рыба, уха"),
    "local": ("местная", "донская кухня, домашняя еда"),
    "georgian": ("грузинская", "хинкали, хачапури, шашлык, харчо"),
    "armenian": ("армянская", "шашлык, хоровац, долма, лаваш"),
    "кавказская": ("кавказская", "шашлык, хинкали, хачапури, люля-кебаб"),
    "uzbek": ("узбекская", "плов, манты, лагман, самса"),
    "oriental": ("восточная", "плов, шашлык, лагман"),
    "arab": ("арабская", "фалафель, хумус, шаурма"),
    "balkan": ("балканская", "плескавица, чевапчичи, гриль"),
    "mediterranean": ("средиземноморская", "паста, рыба, салаты, оливки"),
    "italian": ("итальянская", "пицца, паста, ризотто, лазанья"),
    "italian_pizza": ("итальянская", "пицца"),
    "pizza": ("пицца", "пицца"),
    "french": ("французская", "круассаны, выпечка, кофе"),
    "german": ("немецкая", "колбаски, пиво, рульки"),
    "american": ("американская", "бургеры, стейки, картофель фри"),
    "international": ("европейская", "разные блюда"),
    "fine_dining": ("высокая кухня", "ресторан высокой кухни"),
    "japanese": ("японская", "суши, роллы, рамен"),
    "sushi": ("суши", "суши, роллы"),
    "asian": ("азиатская", "лапша вок, роллы, том ям"),
    "chinese": ("китайская", "лапша, утка, димсамы"),
    "korean": ("корейская", "кимчи, пибимпап, рамён"),
    "beef_bowl": ("азиатская", "рис с говядиной"),
    "burger": ("бургеры", "бургеры, картофель фри"),
    "chicken": ("курица", "жареная курица, наггетсы, крылья"),
    "sandwich": ("сэндвичи", "сэндвичи, шаверма, перекус"),
    "kebab": ("кебаб", "шаурма, шаверма, донер, кебаб"),
    "shawarma": ("шаурма", "шаурма, шаверма, донер"),
    "hot_dog": ("хот-доги", "хот-доги, сосиски"),
    "sausage": ("колбаски", "колбаски, сосиски"),
    "grill": ("гриль", "шашлык, мясо на углях"),
    "barbecue": ("барбекю", "шашлык, мясо на углях"),
    "steak_house": ("стейк-хаус", "стейки, мясо"),
    "fish": ("рыбная", "рыба, раки, уха"),
    "seafood": ("морепродукты", "рыба, креветки, мидии, раки"),
    "fish_and_chips": ("рыба с картошкой", "рыба, картофель фри"),
    "friture": ("фритюр", "картофель фри, жареное"),
    "potato": ("картофель", "печёная картошка, картофель фри"),
    "chebureki": ("чебуреки", "чебуреки, беляши"),
    "pie": ("пироги", "пироги, выпечка"),
    "pancake": ("блины", "блины, блинчики"),
    "savory_pancakes": ("блины", "блины с начинкой"),
    "crepe": ("блины", "блины, крепы"),
    "breakfast": ("завтраки", "завтраки, сырники, яичница, каша"),
    "coffee_shop": ("кофейня", "кофе, капучино, латте, десерты"),
    "tea": ("чайная", "чай, десерты"),
    "cake": ("торты", "торты, пирожные, десерты"),
    "donut": ("пончики", "пончики, десерты"),
    "кондитерская": ("кондитерская", "торты, пирожные, десерты"),
    "sweet-shop": ("сладости", "конфеты, десерты"),
    "deli": ("деликатесы", "деликатесы, закуски"),
    "здоровое_питание": ("здоровое питание", "салаты, боулы, полезная еда"),
    "диетические_продукты": ("диетическая", "полезная еда"),
    "african": ("африканская", "блюда африканской кухни"),
}

_WORD = re.compile(r"[a-zа-яё_-]+")


def kind_label(kind: str) -> str:
    return KIND_LABELS.get(kind, kind.replace("_", " "))


def cuisine_labels(cuisine: str | None) -> list[str]:
    """`russian;pizza` → `['русская', 'пицца']`, неизвестные теги — как есть."""
    if not cuisine:
        return []
    labels = []
    for tag in (t.strip().lower() for t in cuisine.split(";") if t.strip()):
        label = CUISINES.get(tag, (tag.replace("_", " "), ""))[0]
        if label not in labels:
            labels.append(label)
    return labels


def cuisine_dishes(cuisine: str | None) -> list[str]:
    if not cuisine:
        return []
    return [CUISINES[t.strip().lower()][1] for t in cuisine.split(";")
            if t.strip().lower() in CUISINES]


def _stem(word: str) -> str:
    """Грубая основа русского слова: «аптеки», «аптеку» и «аптека» сходятся."""
    return word[:-2] if len(word) > 5 else word[:-1] if len(word) > 3 else word


_BY_LABEL: dict[str, str] = {}
for _kind, _label in KIND_LABELS.items():
    _BY_LABEL.setdefault(_label, _kind)
    _BY_LABEL.setdefault(_stem(_label), _kind)


def resolve_kind(value: str, known: set[str]) -> str | None:
    """Приводит то, что прислала модель, к виду из базы, или возвращает None.

    Принимает OSM-тег (`pharmacy`), русское название в любом падеже
    (`аптеки`) и мелкие вариации написания (`fast food`, `Fast-Food`).
    """
    text = value.strip().lower()
    if not text:
        return None
    for candidate in (text, text.replace(" ", "_"), text.replace("-", "_")):
        if candidate in known:
            return candidate
    if text in _BY_LABEL:
        return _BY_LABEL[text]
    stem = _stem(text)
    if stem in _BY_LABEL:
        return _BY_LABEL[stem]
    for label, kind in _BY_LABEL.items():
        if len(stem) >= 4 and label.startswith(stem):
            return kind
    return None


def suggest_kinds(value: str, known: set[str], limit: int = 6) -> list[str]:
    """Подсказка для модели, когда вид не распознан: ближайшие по написанию."""
    import difflib

    text = value.strip().lower()
    pool = sorted(known)
    by_tag = difflib.get_close_matches(text, pool, n=limit, cutoff=0.5)
    by_label = [_BY_LABEL[m] for m in difflib.get_close_matches(text, list(_BY_LABEL), n=limit, cutoff=0.5)]
    out: list[str] = []
    for kind in by_tag + by_label:
        if kind in known and kind not in out:
            out.append(kind)
    return out[:limit]
