"""Target Hunter: advertisement confidence is not commercial potential."""
from pathlib import Path
import app
from public_hunter import classify
from target_hunter import analyze_target, DEFAULT_TARGET_NICHES

def rank(text):
    return analyze_target(text,classify(text))

def test_solo_manicurist_can_be_ad_but_not_target():
    s="Здравствуйте, милые девушки! Приглашаю на маникюр, акция, запись по телефону +79105734909. Я мастер, работаю в кабинете для пополнения портфолио."
    ad=classify(s)
    assert ad['is_candidate']
    target=analyze_target(s,ad)
    assert target['target_score'] < 60
    assert target['site_evidence']=='not_checked'

def test_b2b_manufacturing_is_prioritized():
    s="Наша компания, собственное производство металлоконструкций. Изготавливаем на заказ. Рассчитаем стоимость, звоните +79991234567."
    a=classify(s)
    assert a['is_candidate']
    assert a['niche']=='industrial'
    t=analyze_target(s,a)
    assert t['target_score'] >= 60
    assert t['target_score'] > rank("Маникюр и педикюр, акция! Записывайтесь по телефону +79991234567.")['target_score']

def test_not_found_website_is_never_claimed():
    t=rank("Компания изготавливает кухни на заказ. Звоните +79991234567.")
    assert t['site_evidence']=='not_checked'
    assert 'сайт' not in t['target_reason'].lower() or 'не найден' not in t['target_reason'].lower()

def test_news_is_not_target():
    t=rank("По данным ведомства произошло ДТП: на место прибыли спасатели, сообщили очевидцы.")
    assert t['target_score']==0

def test_default_niches_exclude_beauty():
    assert 'beauty' not in DEFAULT_TARGET_NICHES
    assert 'construction' in DEFAULT_TARGET_NICHES

def test_ui_has_two_scores_and_filters():
    s=(Path(__file__).resolve().parents[1]/'static'/'app.js').read_text('utf8')
    assert 'target_score' in s and 'targetOnly' in s
    assert 'targetNiche' in s
