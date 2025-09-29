# streamlit app (very small)
import streamlit as st
import pandas as pd
from car_finder import find_cars

st.title("Quick Car Finder — Demo")
zip = st.text_input("City / ZIP", "Dallas TX")
make = st.text_input("Make (optional)")
model = st.text_input("Model (optional)")
min_price = st.number_input("Min price", min_value=0, value=0, step=100)
max_price = st.number_input("Max price", min_value=0, value=15000, step=100)
search = st.button("Search")

if search:
    params = {"zip": zip, "make": make or None, "model": model or None, "min_price": int(min_price) if min_price>0 else None, "max_price": int(max_price) if max_price>0 else None}
    with st.spinner("Searching..."):
        df = find_cars(params)
    if df.empty:
        st.write("No results — try adjusting filters.")
    else:
        st.dataframe(df[["title","price","mileage","location","url","source"]])
        st.download_button("Download CSV", df.to_csv(index=False), "cars.csv")
