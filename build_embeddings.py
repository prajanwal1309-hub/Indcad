from matcher import prepare_and_build_index

if __name__ == '__main__':
    prepare_and_build_index(force_rebuild=True)
    print("Done building NOC embeddings.")
